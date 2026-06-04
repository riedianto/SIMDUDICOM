import os
import glob
import datetime
from celery import Celery
from app.core.config import settings
from app.core.logging_config import logger
from app.services.satusehat_client import satusehat_client
from app.services.webhook import webhook_notifier
from app.database.connection import get_bridge_conn, get_simrs_conn

# Inisialisasi Celery
celery_app = Celery(
    "radiology_tasks",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0"
)

# Konfigurasi Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Jakarta",
    enable_utc=True,
)

def get_satusehat_patient_id(patient_nik_or_id: str, noreg: str = None) -> str:
    """
    Mencari ID Pasien di SATUSEHAT.
    1. Cek apakah input langsung berupa NIK (16 digit).
    2. Cek database SIMRS tbl_pasien untuk NIK dan ihspasien berdasarkan rekmed.
    3. Cek database SIMRS historykunjunganss untuk ihspasien berdasarkan noreg.
    4. Cari ke API SATUSEHAT menggunakan NIK.
    """
    nik = None
    ihs_id = None
    val = patient_nik_or_id.strip()
    
    # 1. Cek apakah input adalah NIK
    if len(val) == 16 and val.isdigit():
        nik = val
    else:
        # Query SIMRS tbl_pasien
        simrs_conn = None
        try:
            simrs_conn = get_simrs_conn()
            cursor = simrs_conn.cursor()
            cursor.execute(
                "SELECT nik, noidentitas, ihspasien FROM tbl_pasien WITH (NOLOCK) WHERE RTRIM(rekmed) = ?", 
                (val,)
            )
            row = cursor.fetchone()
            if row:
                nik_val = str(row[0]).strip() if row[0] else ""
                no_id = str(row[1]).strip() if row[1] else ""
                ihs_val = str(row[2]).strip() if row[2] else ""
                
                if ihs_val:
                    ihs_id = ihs_val
                
                # Gunakan nik atau noidentitas yang valid (16 digit)
                if not nik:
                    if len(nik_val) == 16 and nik_val.isdigit():
                        nik = nik_val
                    elif len(no_id) == 16 and no_id.isdigit():
                        nik = no_id
                        
            # Jika ihs_id belum didapatkan, coba cari dari historykunjunganss
            if not ihs_id and noreg:
                cursor.execute(
                    "SELECT ihspasien FROM historykunjunganss WITH (NOLOCK) WHERE nopendaftaran = ?",
                    (noreg,)
                )
                row_ss = cursor.fetchone()
                if row_ss and row_ss[0]:
                    ihs_id = str(row_ss[0]).strip()
        except Exception as e:
            logger.warning(f"Gagal mencari Patient di database SIMRS: {e}")
        finally:
            if simrs_conn:
                simrs_conn.close()

    if ihs_id:
        logger.info(f"Ditemukan Patient IHS ID langsung dari SIMRS: {ihs_id}")
        return ihs_id

    # 4. Cari ke API SATUSEHAT menggunakan NIK
    if nik:
        try:
            logger.info(f"Mencari ID Pasien SATUSEHAT di API untuk NIK: {nik}")
            result = satusehat_client.search_resource(
                "Patient", 
                {"identifier": f"https://fhir.kemkes.go.id/id/nik|{nik}"}
            )
            if result and "entry" in result:
                satusehat_id = result["entry"][0]["resource"]["id"]
                logger.info(f"Ditemukan Patient ID SATUSEHAT dari API: {satusehat_id} untuk NIK: {nik}")
                return satusehat_id
        except Exception as e:
            logger.warning(f"Gagal mencari Patient NIK di SATUSEHAT API: {e}")

    # Fallback jika tidak ditemukan
    is_sandbox = "sandbox" in settings.SATUSEHAT_BASE_URL.lower()
    if is_sandbox:
        logger.info("Menggunakan dummy Patient ID untuk keperluan testing sandbox.")
        return "P02247346123"
    else:
        raise ValueError(
            f"Patient dengan NIK / RM '{patient_nik_or_id}' belum memiliki IHS ID dan tidak terdaftar di SATUSEHAT."
        )

def create_satusehat_encounter(noreg: str, satusehat_patient_id: str, patient_name: str, performer_doctor_id: str) -> str:
    """
    Membuat Encounter (Kunjungan) baru di SATUSEHAT untuk noreg tertentu jika SIMRS belum mengirimkannya.
    """
    logger.info(f"Mencoba membuat Encounter baru secara dinamis untuk noreg: {noreg}")
    
    # Default to rawat jalan (AMB)
    encounter_payload = {
        "resourceType": "Encounter",
        "status": "arrived",
        "statusHistory": [
            {
                "status": "arrived",
                "period": {
                    "start": datetime.datetime.now().strftime("%Y-%m-%dT00:00:00+07:00")
                }
            }
        ],
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
            "display": "ambulatory"
        },
        "subject": {
            "reference": f"Patient/{satusehat_patient_id}",
            "display": patient_name
        },
        "participant": [
            {
                "type": [
                    {
                        "coding": [
                            {
                                "system": "http://terminology.hl7.org/CodeSystem/v3-ParticipationType",
                                "code": "ATND",
                                "display": "attender"
                            }
                        ]
                    }
                ],
                "individual": {
                    "reference": f"Practitioner/{performer_doctor_id}",
                    "display": "dr. SOSOR TUAH I.P.T, Sp.Rad"
                }
            }
        ],
        "period": {
            "start": datetime.datetime.now().strftime("%Y-%m-%dT00:00:00+07:00")
        },
        "location": [
            {
                "location": {
                    "reference": "Location/b9a42549-2eaa-48b7-8f57-c51f38a18ec3",
                    "display": "Poliklinik Penyakit  Dalam"
                }
            }
        ],
        "serviceProvider": {
            "reference": f"Organization/{settings.SATUSEHAT_ORGANIZATION_ID}"
        },
        "identifier": [
            {
                "system": f"http://sys-ids.kemkes.go.id/encounter/{settings.SATUSEHAT_ORGANIZATION_ID}",
                "value": noreg
            }
        ]
    }
    
    try:
        response = satusehat_client.post_resource("Encounter", encounter_payload)
        encounter_id = response["id"]
        logger.info(f"Encounter baru berhasil dibuat secara dinamis: {encounter_id}")
        
        # Simpan ke historykunjunganss di SIMRS
        simrs_conn = None
        try:
            simrs_conn = get_simrs_conn()
            cursor = simrs_conn.cursor()
            cursor.execute(
                """
                INSERT INTO historykunjunganss (nopendaftaran, encounter, responcode, respontext, ihspasien, tanggalkirim)
                VALUES (?, ?, '200', 'Encounter created dynamically by SIMDUDICOM Bridge', ?, GETDATE())
                """,
                (noreg, encounter_id, satusehat_patient_id)
            )
            simrs_conn.commit()
            logger.info(f"Encounter ID {encounter_id} berhasil dicatat di historykunjunganss SIMRS.")
        except Exception as db_err:
            logger.warning(f"Gagal mencatat Encounter ke historykunjunganss: {db_err}")
        finally:
            if simrs_conn:
                simrs_conn.close()
                
        return encounter_id
    except Exception as e:
        logger.error(f"Gagal membuat Encounter dinamis di SATUSEHAT API: {e}")
        raise e

def get_satusehat_encounter_id(
    accession_number: str, 
    noreg: str, 
    satusehat_patient_id: str = None, 
    patient_name: str = None, 
    performer_doctor_id: str = None
) -> str:
    """
    Mendapatkan Encounter ID SATUSEHAT.
    1. Cari di table historykunjunganss di database SIMRS.
    2. Cari langsung ke API SATUSEHAT menggunakan identifier noreg.
    3. Jika tidak ada di Production, buat secara dinamis.
    """
    encounter_id = None
    
    if noreg:
        simrs_conn = None
        try:
            simrs_conn = get_simrs_conn()
            cursor = simrs_conn.cursor()
            # Cari di historykunjunganss
            cursor.execute(
                "SELECT encounter FROM historykunjunganss WITH (NOLOCK) WHERE nopendaftaran = ?",
                (noreg,)
            )
            ss_row = cursor.fetchone()
            if ss_row and ss_row[0]:
                encounter_id = str(ss_row[0]).strip()
                logger.info(f"Ditemukan Encounter ID dari historykunjunganss: {encounter_id}")
        except Exception as e:
            logger.warning(f"Gagal mencari Encounter di database SIMRS: {e}")
        finally:
            if simrs_conn:
                simrs_conn.close()

        # 2. Cari ke API SATUSEHAT menggunakan identifier noreg
        if not encounter_id:
            try:
                system_url = f"http://sys-ids.kemkes.go.id/encounter/{settings.SATUSEHAT_ORGANIZATION_ID}"
                logger.info(f"Mencari Encounter di SATUSEHAT API dengan: {system_url}|{noreg}")
                res = satusehat_client.search_resource("Encounter", {"identifier": f"{system_url}|{noreg}"})
                if res and res.get("total", 0) > 0:
                    encounter_id = res["entry"][0]["resource"]["id"]
                    logger.info(f"Ditemukan Encounter ID dari SATUSEHAT API: {encounter_id}")
            except Exception as e:
                logger.warning(f"Gagal mencari Encounter di SATUSEHAT API: {e}")

    if encounter_id:
        return encounter_id

    is_sandbox = "sandbox" in settings.SATUSEHAT_BASE_URL.lower()
    if is_sandbox:
        logger.info("Menggunakan dummy Encounter ID untuk keperluan testing sandbox.")
        return "28230129-2d1f-4efc-8e0f-90e632d4314c"

    # Di Production, coba buat Encounter dinamis jika parameter lengkap
    if noreg and satusehat_patient_id and performer_doctor_id:
        try:
            return create_satusehat_encounter(
                noreg=noreg,
                satusehat_patient_id=satusehat_patient_id,
                patient_name=patient_name or "Pasien",
                performer_doctor_id=performer_doctor_id
            )
        except Exception as dynamic_err:
            logger.error(f"Gagal membuat Encounter dinamis: {dynamic_err}")

    raise ValueError(
        f"Kunjungan (Encounter) untuk pendaftaran '{noreg}' belum dikirim/terintegrasi ke SATUSEHAT. "
        f"Silakan kirim data kunjungan/registrasi SIMRS ke SATUSEHAT terlebih dahulu."
    )

def get_satusehat_practitioner_id(doctor_name: str) -> str:
    """
    Mendapatkan IHS ID Dokter (Practitioner) dari tbl_dokter di SIMRS.
    Jika tidak ada di database, coba cari ke API SATUSEHAT menggunakan NIK dokter.
    """
    if not doctor_name:
        return "10002923719" # default Sp.Rad Dr. Sosor Tuah
        
    ihs_id = None
    nik = None
    
    simrs_conn = None
    try:
        simrs_conn = get_simrs_conn()
        cursor = simrs_conn.cursor()
        cursor.execute(
            "SELECT ihspegawai, nik FROM tbl_dokter WITH (NOLOCK) WHERE RTRIM(nadokter) = ? OR RTRIM(nadokter) LIKE ?",
            (doctor_name.strip(), f"%{doctor_name.strip()}%")
        )
        row = cursor.fetchone()
        if row:
            if row[0]:
                ihs_id = str(row[0]).strip()
            if row[1]:
                nik = str(row[1]).strip()
    except Exception as e:
        logger.warning(f"Gagal mencari dokter di tbl_dokter: {e}")
    finally:
        if simrs_conn:
            simrs_conn.close()
            
    if ihs_id:
        return ihs_id
        
    # Jika ada NIK tapi belum ada IHS ID lokal, cari ke SATUSEHAT API
    if nik and len(nik) == 16 and nik.isdigit():
        try:
            logger.info(f"Mencari Practitioner ID di SATUSEHAT API untuk NIK Dokter: {nik}")
            result = satusehat_client.search_resource(
                "Practitioner",
                {"identifier": f"https://fhir.kemkes.go.id/id/nik|{nik}"}
            )
            if result and "entry" in result:
                practitioner_id = result["entry"][0]["resource"]["id"]
                logger.info(f"Ditemukan Practitioner ID dari API: {practitioner_id}")
                return practitioner_id
        except Exception as e:
            logger.warning(f"Gagal mencari Practitioner NIK di SATUSEHAT API: {e}")
            
    # Default fallback ke Dr. Sosor Tuah Sp.Rad jika gagal
    return "10002923719"
 


def log_integration_attempt(accession, resource_type, action, status, request_payload, response_payload, error_msg=None):
    """
    Mencatat log transaksi pengiriman ke tabel integration_logs
    """
    conn = None
    try:
        conn = get_bridge_conn()
        cursor = conn.cursor()
        query = """
            INSERT INTO integration_logs (
                accession_number, resource_type, action_type, status, 
                request_payload, response_payload, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, GETDATE())
        """
        cursor.execute(
            query, 
            (accession, resource_type, action, status, str(request_payload), str(response_payload), error_msg)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Gagal menulis audit log integrasi ke DB lokal: {e}")
    finally:
        if conn:
            conn.close()

@celery_app.task(bind=True, max_retries=5, default_retry_delay=60)
def send_servicerequest_task(self, accession_number: str):
    """
    Task asinkron untuk membuat resource ServiceRequest di SATUSEHAT.
    Hanya mengirim jika status masih UNSENT atau FAILED.
    Jika sudah SENT, langsung skip (idempoten).
    """
    logger.info(f"[Celery] Memulai pengiriman ServiceRequest untuk: {accession_number}")
    
    # 1. Ambil data order dari database lokal
    bridge_conn = None
    order = None
    try:
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        cursor.execute(
            """
            SELECT patient_id, patient_name, birth_date, gender, doctor_name, modality, procedure_name, order_datetime,
                   satusehat_servicerequest_status, satusehat_servicerequest_id
            FROM radiology_orders WHERE accession_number = ?
            """, 
            (accession_number,)
        )
        row = cursor.fetchone()
        if row:
            order = {
                "patient_id": row[0],
                "patient_name": row[1],
                "birth_date": row[2],
                "gender": row[3],
                "doctor_name": row[4],
                "modality": row[5],
                "procedure_name": row[6],
                "order_datetime": row[7],
                "servicerequest_status": str(row[8]).strip() if row[8] else "UNSENT",
                "servicerequest_id": str(row[9]).strip() if row[9] else None
            }
    except Exception as e:
        logger.error(f"Gagal mengambil data order {accession_number} dari DB lokal: {e}")
        raise self.retry(exc=e)
    finally:
        if bridge_conn:
            bridge_conn.close()

    if not order:
        logger.error(f"Order {accession_number} tidak ditemukan di database lokal, skip task.")
        return

    # --- IDEMPOTENCY CHECK: Skip jika sudah berhasil dikirim sebelumnya ---
    if order["servicerequest_status"] == "SENT" and order["servicerequest_id"]:
        logger.info(
            f"ServiceRequest untuk {accession_number} sudah pernah dikirim "
            f"(ID: {order['servicerequest_id']}). Skip pengiriman ulang."
        )
        return

    # 2. Ambil noreg dari tbl_hradio di SIMRS
    noreg = None
    simrs_conn = None
    try:
        simrs_conn = get_simrs_conn()
        cursor = simrs_conn.cursor()
        cursor.execute("SELECT noreg FROM tbl_hradio WITH (NOLOCK) WHERE noradio = ?", (accession_number,))
        row = cursor.fetchone()
        if row and row[0]:
            noreg = str(row[0]).strip()
    except Exception as e:
        logger.warning(f"Gagal mengambil noreg dari tbl_hradio untuk {accession_number}: {e}")
    finally:
        if simrs_conn:
            simrs_conn.close()

    # 3. Cari Patient ID dan Dokter
    satusehat_patient_id = get_satusehat_patient_id(order["patient_id"], noreg)
    requester_doctor_id = get_satusehat_practitioner_id(order["doctor_name"])
    performer_doctor_id = get_satusehat_practitioner_id("dr. SOSOR TUAH I.P.T, Sp.Rad")

    # 4. Cari atau buat Encounter ID
    satusehat_encounter_id = get_satusehat_encounter_id(
        accession_number=accession_number,
        noreg=noreg,
        satusehat_patient_id=satusehat_patient_id,
        patient_name=order["patient_name"],
        performer_doctor_id=performer_doctor_id
    )

    # Format authoredOn dengan timezone offset +07:00
    if order["order_datetime"]:
        authored_on = order["order_datetime"].strftime("%Y-%m-%dT%H:%M:%S+07:00")
    else:
        authored_on = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S+07:00")

    # 4. Bentuk Payload FHIR ServiceRequest
    # Menggunakan standar Kemenkes RI
    fhir_payload = {
        "resourceType": "ServiceRequest",
        "identifier": [
            {
                "system": f"http://sys-ids.kemkes.go.id/acsn/{settings.SATUSEHAT_ORGANIZATION_ID}",
                "value": accession_number
            }
        ],
        "status": "active",
        "intent": "order",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": "363679005",
                        "display": "Imaging procedure"
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    # Gunakan kode LOINC default jika tidak terdefinisi (contoh: Chest X-Ray)
                    "code": "24606-6",
                    "display": f"Radiology study of {order['procedure_name']}"
                }
            ],
            "text": order["procedure_name"]
        },
        "subject": {
            "reference": f"Patient/{satusehat_patient_id}",
            "display": order["patient_name"]
        },
        "encounter": {
            "reference": f"Encounter/{satusehat_encounter_id}",
            "display": f"Kunjungan Pasien Noreg {noreg}" if noreg else "Kunjungan Pasien"
        },
        "authoredOn": authored_on,
        "requester": {
            "reference": f"Practitioner/{requester_doctor_id}",
            "display": order["doctor_name"]
        },
        "performer": [
            {
                "reference": f"Practitioner/{performer_doctor_id}",
                "display": "dr. SOSOR TUAH I.P.T, Sp.Rad"
            }
        ]
    }

    # 4. Kirim ke SATUSEHAT
    try:
        response = satusehat_client.post_resource("ServiceRequest", fhir_payload)
        satusehat_id = response["id"]
        
        # 5. Update status di database lokal
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        cursor.execute(
            """
            UPDATE radiology_orders 
            SET satusehat_servicerequest_id = ?, 
                satusehat_servicerequest_status = 'SENT',
                updated_at = GETDATE()
            WHERE accession_number = ?
            """,
            (satusehat_id, accession_number)
        )
        bridge_conn.commit()
        bridge_conn.close()
        
        logger.info(f"ServiceRequest berhasil didaftarkan ke SATUSEHAT: ID={satusehat_id} untuk Accession={accession_number}")
        
        # Audit Log
        log_integration_attempt(accession_number, "ServiceRequest", "POST", "SUCCESS", fhir_payload, response)
        
    except Exception as e:
        logger.error(f"Gagal mengirim ServiceRequest {accession_number} ke SATUSEHAT: {e}")
        
        # Penanganan Error Duplicate (Kemenkes Rule 20002)
        is_duplicate = False
        if hasattr(e, "response") and e.response is not None:
            try:
                body = e.response.json()
                if "duplicate" in str(body).lower() or "20002" in str(body):
                    is_duplicate = True
            except Exception:
                pass
        if "duplicate" in str(e).lower() or "20002" in str(e):
            is_duplicate = True
            
        if is_duplicate:
            logger.info(f"Mendeteksi duplicate ServiceRequest untuk {accession_number}. Melakukan lookup...")
            try:
                system_url = f"http://sys-ids.kemkes.go.id/acsn/{settings.SATUSEHAT_ORGANIZATION_ID}"
                res = satusehat_client.search_resource("ServiceRequest", {"identifier": f"{system_url}|{accession_number}"})
                if res and res.get("total", 0) > 0:
                    satusehat_id = res["entry"][0]["resource"]["id"]
                    logger.info(f"Ditemukan existing ServiceRequest ID: {satusehat_id}")
                    
                    bridge_conn = get_bridge_conn()
                    cursor = bridge_conn.cursor()
                    cursor.execute(
                        """
                        UPDATE radiology_orders 
                        SET satusehat_servicerequest_id = ?, 
                            satusehat_servicerequest_status = 'SENT',
                            updated_at = GETDATE()
                        WHERE accession_number = ?
                        """,
                        (satusehat_id, accession_number)
                    )
                    bridge_conn.commit()
                    bridge_conn.close()
                    
                    log_integration_attempt(accession_number, "ServiceRequest", "POST", "SUCCESS", fhir_payload, {"id": satusehat_id, "note": "Resolved duplicate via lookup"})
                    return
            except Exception as lookup_err:
                logger.error(f"Gagal lookup ServiceRequest duplikat: {lookup_err}")
                
        # Catat kegagalan jika bukan duplicate
        log_integration_attempt(accession_number, "ServiceRequest", "POST", "FAILED", fhir_payload, None, str(e))
        
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        cursor.execute(
            "UPDATE radiology_orders SET satusehat_servicerequest_status = 'FAILED', updated_at = GETDATE() WHERE accession_number = ?",
            (accession_number,)
        )
        bridge_conn.commit()
        bridge_conn.close()

@celery_app.task(bind=True, max_retries=5, default_retry_delay=60)
def send_imagingstudy_task(self, study_instance_uid: str):
    """
    Task asinkron untuk membuat resource ImagingStudy di SATUSEHAT,
    kemudian upload file DICOM fisik via STOW-RS (DICOMweb).
    
    Urutan sesuai Panduan Kemenkes (Hal. 20, Cetak Biru 1):
    1. POST ImagingStudy (FHIR) → dapatkan imagingstudy_id
    2. Upload file .dcm via STOW-RS dengan header X-ImagingStudy-ID
    3. Kirim Webhook callback ke SIMRS
    """
    logger.info(f"[Celery] Memulai pengiriman ImagingStudy untuk StudyInstanceUID: {study_instance_uid}")
    
    bridge_conn = None
    study_data = None
    try:
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        
        # Ambil data DICOM Study
        cursor.execute(
            "SELECT accession_number, series_count, sop_count, storage_path FROM dicom_studies WHERE study_instance_uid = ?",
            (study_instance_uid,)
        )
        row = cursor.fetchone()
        if row:
            study_data = {
                "accession_number": row[0],
                "series_count": row[1],
                "sop_count": row[2],
                "storage_path": row[3]
            }
    except Exception as e:
        logger.error(f"Gagal mengambil data dicom_study {study_instance_uid} dari DB lokal: {e}")
        raise self.retry(exc=e)
    finally:
        if bridge_conn:
            bridge_conn.close()

    if not study_data:
        logger.error(f"StudyInstanceUID {study_instance_uid} tidak ditemukan di database lokal.")
        return

    # Ambil order terelasi untuk mendapatkan Patient ID & ServiceRequest ID
    accession = study_data["accession_number"]
    order_data = None
    try:
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        cursor.execute(
            """
            SELECT satusehat_servicerequest_id, patient_id, patient_name, gender, modality, procedure_name
            FROM radiology_orders WHERE accession_number = ?
            """,
            (accession,)
        )
        row = cursor.fetchone()
        if row:
            order_data = {
                "servicerequest_id": row[0],
                "patient_id": row[1],
                "patient_name": row[2],
                "gender": row[3],
                "modality": row[4],
                "procedure_name": row[5]
            }
    except Exception as e:
        logger.error(f"Gagal mengambil data order terelasi untuk {accession}: {e}")
        return
    finally:
        if bridge_conn:
            bridge_conn.close()

    if not order_data or not order_data["servicerequest_id"]:
        logger.warning(f"ServiceRequest ID tidak ditemukan untuk accession {accession}. Menghentikan task ImagingStudy...")
        return

    # Ambil noreg dari tbl_hradio di SIMRS
    noreg = None
    simrs_conn = None
    try:
        simrs_conn = get_simrs_conn()
        cursor = simrs_conn.cursor()
        cursor.execute("SELECT noreg FROM tbl_hradio WITH (NOLOCK) WHERE noradio = ?", (accession,))
        row = cursor.fetchone()
        if row and row[0]:
            noreg = str(row[0]).strip()
    except Exception as e:
        logger.warning(f"Gagal mengambil noreg dari tbl_hradio untuk {accession}: {e}")
    finally:
        if simrs_conn:
            simrs_conn.close()

    satusehat_patient_id = get_satusehat_patient_id(order_data["patient_id"], noreg)
    performer_doctor_id = get_satusehat_practitioner_id("dr. SOSOR TUAH I.P.T, Sp.Rad")
    satusehat_encounter_id = get_satusehat_encounter_id(
        accession_number=accession,
        noreg=noreg,
        satusehat_patient_id=satusehat_patient_id,
        patient_name=order_data["patient_name"],
        performer_doctor_id=performer_doctor_id
    )

    # Map non-standard modality code 'DR' to standard DICOM code 'DX'
    raw_modality = order_data.get("modality", "CR")
    if raw_modality:
        raw_modality = str(raw_modality).strip()
    std_modality = "DX" if raw_modality == "DR" else (raw_modality if raw_modality else "CR")

    # Read physical DICOM files to dynamically construct series and instances
    import pydicom
    storage_base = f"/storage/dicom/{study_instance_uid}"
    dcm_files = []
    if os.path.exists(storage_base):
        dcm_files = glob.glob(os.path.join(storage_base, "**", "*.dcm"), recursive=True)
    if not dcm_files:
        alt_pattern = f"/storage/dicom/**/*.dcm"
        all_files = glob.glob(alt_pattern, recursive=True)
        for f in all_files:
            if study_instance_uid in f:
                dcm_files.append(f)

    series_dict = {}
    if dcm_files:
        for dcm_path in dcm_files:
            try:
                ds = pydicom.dcmread(dcm_path, stop_before_pixels=True)
                ser_uid = getattr(ds, "SeriesInstanceUID", f"{study_instance_uid}.1")
                sop_uid = getattr(ds, "SOPInstanceUID", f"{study_instance_uid}.1.1")
                sop_class_uid = getattr(ds, "SOPClassUID", "1.2.840.10008.5.1.4.1.1.7") # Default to Secondary Capture
                ser_number = int(getattr(ds, "SeriesNumber", 1))
                
                sop_class_code = f"urn:oid:{sop_class_uid}"
                
                if ser_uid not in series_dict:
                    series_dict[ser_uid] = {
                        "uid": ser_uid,
                        "number": ser_number,
                        "modality": {
                            "system": "http://dicom.nema.org/resources/ontology/DCM",
                            "code": std_modality
                        },
                        "description": order_data["procedure_name"],
                        "instance": []
                    }
                
                instance_uids = [inst["uid"] for inst in series_dict[ser_uid]["instance"]]
                if sop_uid not in instance_uids:
                    series_dict[ser_uid]["instance"].append({
                        "uid": sop_uid,
                        "sopClass": {
                            "system": "urn:ietf:rfc:3986",
                            "code": sop_class_code
                        }
                    })
            except Exception as dcm_read_err:
                logger.warning(f"Gagal membaca file DICOM untuk metadata {dcm_path}: {dcm_read_err}")

    if not series_dict:
        series_dict[f"{study_instance_uid}.1"] = {
            "uid": f"{study_instance_uid}.1",
            "number": 1,
            "modality": {
                "system": "http://dicom.nema.org/resources/ontology/DCM",
                "code": std_modality
            },
            "description": order_data["procedure_name"],
            "instance": [
                {
                    "uid": f"{study_instance_uid}.1.1",
                    "sopClass": {
                        "system": "urn:ietf:rfc:3986",
                        "code": "urn:oid:1.2.840.10008.5.1.4.1.1.7"
                    }
                }
            ]
        }

    series_payload = list(series_dict.values())

    # 3. Bentuk Payload FHIR ImagingStudy
    fhir_payload = {
        "resourceType": "ImagingStudy",
        "status": "available",
        "modality": [
            {
                "system": "http://dicom.nema.org/resources/ontology/DCM",
                "code": std_modality
            }
        ],
        "subject": {
            "reference": f"Patient/{satusehat_patient_id}",
            "display": order_data["patient_name"]
        },
        "encounter": {
            "reference": f"Encounter/{satusehat_encounter_id}"
        },
        "basedOn": [
            {
                "reference": f"ServiceRequest/{order_data['servicerequest_id']}"
            }
        ],
        "started": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S+07:00"), # Waktu mulai pemeriksaan dengan offset
        "numberOfSeries": len(series_payload),
        "numberOfInstances": sum(len(s["instance"]) for s in series_payload),
        "identifier": [
            {
                "use": "official",
                "system": "urn:dicom:uid",
                "value": f"urn:oid:{study_instance_uid}"
            }
        ],
        "series": series_payload
    }

    # 4. Kirim ImagingStudy (FHIR) ke SATUSEHAT
    imagingstudy_id = None
    try:
        try:
            response = satusehat_client.post_resource("ImagingStudy", fhir_payload)
            imagingstudy_id = response["id"]
        except Exception as img_err:
            is_duplicate = False
            if hasattr(img_err, "response") and img_err.response is not None:
                try:
                    body = img_err.response.json()
                    if "duplicate" in str(body).lower() or "20002" in str(body):
                        is_duplicate = True
                except Exception:
                    pass
            if "duplicate" in str(img_err).lower() or "20002" in str(img_err):
                is_duplicate = True
                
            if is_duplicate:
                logger.info(f"Mendeteksi duplicate ImagingStudy untuk {accession}. Melakukan lookup...")
                res = satusehat_client.search_resource("ImagingStudy", {
                    "identifier": f"urn:dicom:uid|urn:oid:{study_instance_uid}"
                })
                if res and res.get("total", 0) > 0:
                    imagingstudy_id = res["entry"][0]["resource"]["id"]
                    logger.info(f"Ditemukan existing ImagingStudy ID: {imagingstudy_id}")
                    response = {"id": imagingstudy_id, "note": "Resolved duplicate via lookup"}
                else:
                    raise img_err
            else:
                raise img_err
        
        # Update status di database lokal → FHIR_SENT
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        
        cursor.execute(
            "UPDATE dicom_studies SET satusehat_status = 'FHIR_SENT' WHERE study_instance_uid = ?",
            (study_instance_uid,)
        )
        cursor.execute(
            "UPDATE radiology_orders SET satusehat_imagingstudy_id = ?, updated_at = GETDATE() WHERE accession_number = ?",
            (imagingstudy_id, accession)
        )
        
        bridge_conn.commit()
        bridge_conn.close()
        
        logger.info(f"ImagingStudy berhasil didaftarkan ke SATUSEHAT: ID={imagingstudy_id} untuk Accession={accession}")
        log_integration_attempt(accession, "ImagingStudy", "POST", "SUCCESS", fhir_payload, response)
        
    except Exception as e:
        logger.error(f"Gagal mengirim ImagingStudy {study_instance_uid} ke SATUSEHAT: {e}")
        log_integration_attempt(accession, "ImagingStudy", "POST", "FAILED", fhir_payload, None, str(e))
        
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        cursor.execute(
            "UPDATE dicom_studies SET satusehat_status = 'FAILED' WHERE study_instance_uid = ?",
            (study_instance_uid,)
        )
        bridge_conn.commit()
        bridge_conn.close()

        # Webhook: notifikasi FAILED ke SIMRS
        if webhook_notifier.is_configured:
            try:
                webhook_notifier.send_notification(
                    accession_number=accession,
                    status="FAILED",
                    error_code="FHIR_IMAGINGSTUDY_FAILED",
                    message=f"Gagal mengirim ImagingStudy ke SATUSEHAT: {str(e)}"
                )
                log_integration_attempt(accession, "Webhook", "POST", "SENT", 
                                        {"accessionNumber": accession, "status": "FAILED"}, None)
            except Exception as wh_err:
                logger.error(f"Gagal memproses webhook: {wh_err}")

    # ====================================================================
    # 5. Upload file DICOM fisik via STOW-RS (DICOMweb)
    # Panduan Kemenkes Hal. 20 (Cetak Biru 1, langkah 5):
    # "Kirim file .dcm satu per satu ke endpoint DICOM SATUSEHAT
    #  sambil menyertakan header X-ImagingStudy-ID"
    # ====================================================================
    if not imagingstudy_id:
        logger.error(f"ImagingStudy ID tidak tersedia untuk STOW-RS upload. Accession: {accession}")
        return

    # Ambil storage_path dari database (folder accession, bukan file individual)
    dcm_files = []
    storage_base = None
    try:
        _sc = get_bridge_conn()
        _cur = _sc.cursor()
        _cur.execute("SELECT storage_path FROM dicom_studies WHERE study_instance_uid = ?", (study_instance_uid,))
        _row = _cur.fetchone()
        _sc.close()
        if _row and _row[0]:
            storage_base = str(_row[0]).strip()
    except Exception as _e:
        logger.warning(f"Gagal baca storage_path dari DB: {_e}")

    # Fallback ke path lama jika DB tidak ada
    if not storage_base or not os.path.exists(storage_base):
        storage_base = f"/storage/dicom/{study_instance_uid}"

    if os.path.exists(storage_base):
        if storage_base.endswith('.dcm'):
            dcm_files = [storage_base]
        else:
            dcm_files = glob.glob(os.path.join(storage_base, "**", "*.dcm"), recursive=True)
    if not dcm_files:
        alt_pattern = f"/storage/dicom/**/*.dcm"
        all_files = glob.glob(alt_pattern, recursive=True)
        for f in all_files:
            basename = os.path.basename(f)
            if study_instance_uid in f or basename == f"{accession}.dcm":
                dcm_files.append(f)

    if not dcm_files:
        logger.warning(f"Tidak ada file .dcm ditemukan di {storage_base} untuk upload STOW-RS")
        # Update status tetap sebagai FHIR_SENT (tidak ada file untuk diupload)
        return

    logger.info(f"[STOW-RS] Memulai upload {len(dcm_files)} file DICOM untuk ImagingStudy {imagingstudy_id}")

    upload_success = 0
    upload_failed = 0
    last_error = None

    for dcm_path in dcm_files:
        try:
            result = satusehat_client.upload_dicom_stowrs(imagingstudy_id, dcm_path)
            upload_success += 1
            log_integration_attempt(
                accession, "STOW-RS", "POST", "SUCCESS",
                {"file": os.path.basename(dcm_path), "imagingstudy_id": imagingstudy_id},
                result
            )
        except Exception as upload_err:
            upload_failed += 1
            last_error = str(upload_err)
            logger.error(f"[STOW-RS] Gagal upload file {dcm_path}: {upload_err}")
            log_integration_attempt(
                accession, "STOW-RS", "POST", "FAILED",
                {"file": os.path.basename(dcm_path), "imagingstudy_id": imagingstudy_id},
                None, str(upload_err)
            )

    # 6. Update status final di database lokal
    final_status = "UPLOADED" if upload_failed == 0 else "UPLOAD_PARTIAL"
    bridge_conn = get_bridge_conn()
    cursor = bridge_conn.cursor()
    cursor.execute(
        "UPDATE dicom_studies SET satusehat_status = ? WHERE study_instance_uid = ?",
        (final_status, study_instance_uid)
    )
    bridge_conn.commit()
    bridge_conn.close()

    logger.info(
        f"[STOW-RS] Upload selesai untuk Accession {accession}: "
        f"Sukses={upload_success}, Gagal={upload_failed}, Status={final_status}"
    )

    # 7. Kirim Webhook callback ke SIMRS
    if upload_failed == 0:
        if webhook_notifier.is_configured:
            try:
                webhook_notifier.send_notification(
                    accession_number=accession,
                    status="SUCCESS",
                    message=f"ImagingStudy dan {upload_success} file DICOM berhasil dikirim ke SATUSEHAT"
                )
                log_integration_attempt(accession, "Webhook", "POST", "SENT",
                                        {"accessionNumber": accession, "status": "SUCCESS"}, None)
            except Exception as wh_err:
                logger.error(f"Gagal memproses webhook: {wh_err}")
    else:
        if webhook_notifier.is_configured:
            try:
                webhook_notifier.send_notification(
                    accession_number=accession,
                    status="FAILED",
                    error_code="STOWRS_PARTIAL_FAILURE",
                    message=f"Upload DICOM gagal sebagian: {upload_success} sukses, {upload_failed} gagal. Error: {last_error}"
                )
                log_integration_attempt(accession, "Webhook", "POST", "SENT",
                                        {"accessionNumber": accession, "status": "FAILED"}, None)
            except Exception as wh_err:
                logger.error(f"Gagal memproses webhook: {wh_err}")


@celery_app.task(bind=True, max_retries=5, default_retry_delay=60)
def send_report_task(self, accession_number: str):
    """
    Task asinkron untuk mengirimkan Observation dan DiagnosticReport
    setelah pemeriksaan selesai dan ekspertise diisi di SIMRS.
    Hanya mengirim jika status masih UNSENT atau FAILED.
    Jika sudah SENT, langsung skip (idempoten).
    """
    logger.info(f"[Celery] Memulai pengiriman laporan hasil radiologi (DiagnosticReport) untuk: {accession_number}")

    # 1. Ambil data order lokal
    bridge_conn = None
    order_data = None
    try:
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        cursor.execute(
            """
            SELECT satusehat_servicerequest_id, satusehat_imagingstudy_id, patient_id, patient_name, gender, procedure_name,
                   satusehat_report_status
            FROM radiology_orders WHERE accession_number = ?
            """,
            (accession_number,)
        )
        row = cursor.fetchone()
        if row:
            order_data = {
                "servicerequest_id": row[0],
                "imagingstudy_id": row[1],
                "patient_id": row[2],
                "patient_name": row[3],
                "gender": row[4],
                "procedure_name": row[5],
                "report_status": str(row[6]).strip() if row[6] else "UNSENT"
            }
    except Exception as e:
        logger.error(f"Gagal mengambil data order {accession_number}: {e}")
        return
    finally:
        if bridge_conn:
            bridge_conn.close()

    if not order_data or not order_data["servicerequest_id"]:
        logger.error(f"Tidak dapat mengirim report. Order {accession_number} belum memiliki ServiceRequest ID.")
        return

    # --- IDEMPOTENCY CHECK: Skip jika laporan sudah berhasil dikirim sebelumnya ---
    if order_data["report_status"] == "SENT":
        logger.info(
            f"DiagnosticReport untuk {accession_number} sudah pernah dikirim. "
            f"Skip pengiriman ulang."
        )
        return

    # 2. Ambil ekspertise/hasil dari SIMRS artha_medika
    simrs_conn = None
    hasil_ekspertise = ""
    try:
        simrs_conn = get_simrs_conn()
        cursor = simrs_conn.cursor()
        cursor.execute("SELECT expertise FROM tbl_radioexpert WITH (NOLOCK) WHERE noradio = ?", (accession_number,))
        row = cursor.fetchone()
        if row and row[0]:
            hasil_ekspertise = str(row[0]).strip()
    except Exception as e:
        logger.error(f"Gagal mengambil ekspertise SIMRS untuk {accession_number}: {e}")
        return
    finally:
        if simrs_conn:
            simrs_conn.close()

    if not hasil_ekspertise:
        logger.warning(f"Hasil ekspertise untuk {accession_number} masih kosong di SIMRS, skip.")
        return

    # Ambil noreg dari tbl_hradio di SIMRS
    noreg = None
    simrs_conn = None
    try:
        simrs_conn = get_simrs_conn()
        cursor = simrs_conn.cursor()
        cursor.execute("SELECT noreg FROM tbl_hradio WITH (NOLOCK) WHERE noradio = ?", (accession_number,))
        row = cursor.fetchone()
        if row and row[0]:
            noreg = str(row[0]).strip()
    except Exception as e:
        logger.warning(f"Gagal mengambil noreg dari tbl_hradio untuk {accession_number}: {e}")
    finally:
        if simrs_conn:
            simrs_conn.close()

    satusehat_patient_id = get_satusehat_patient_id(order_data["patient_id"], noreg)
    performer_doctor_id = get_satusehat_practitioner_id("dr. SOSOR TUAH I.P.T, Sp.Rad")
    satusehat_encounter_id = get_satusehat_encounter_id(
        accession_number=accession_number,
        noreg=noreg,
        satusehat_patient_id=satusehat_patient_id,
        patient_name=order_data["patient_name"],
        performer_doctor_id=performer_doctor_id
    )

    # 3. Buat Resource Observation untuk Ekspertise (Hasil Bacaan)
    observation_payload = {
        "resourceType": "Observation",
        "status": "final",
        "performer": [
            {
                "reference": f"Practitioner/{performer_doctor_id}",
                "display": "dr. SOSOR TUAH I.P.T, Sp.Rad"
            }
        ],
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "imaging",
                        "display": "Imaging"
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": "19005-8",
                    "display": "Radiology Diagnostic report impression"
                }
            ]
        },
        "subject": {
            "reference": f"Patient/{satusehat_patient_id}",
            "display": order_data["patient_name"]
        },
        "encounter": {
            "reference": f"Encounter/{satusehat_encounter_id}"
        },
        "valueString": hasil_ekspertise
    }

    observation_id = None
    try:
        # POST Observation ke SATUSEHAT
        try:
            obs_res = satusehat_client.post_resource("Observation", observation_payload)
            observation_id = obs_res["id"]
            logger.info(f"Observation berhasil didaftarkan: ID={observation_id}")
            log_integration_attempt(accession_number, "Observation", "POST", "SUCCESS", observation_payload, obs_res)
        except Exception as obs_err:
            is_duplicate = False
            if hasattr(obs_err, "response") and obs_err.response is not None:
                try:
                    body = obs_err.response.json()
                    if "duplicate" in str(body).lower() or "20002" in str(body):
                        is_duplicate = True
                except Exception:
                    pass
            if "duplicate" in str(obs_err).lower() or "20002" in str(obs_err):
                is_duplicate = True
                
            if is_duplicate:
                logger.info(f"Mendeteksi duplicate Observation untuk {accession_number}. Melakukan lookup...")
                res = satusehat_client.search_resource("Observation", {
                    "subject": f"Patient/{satusehat_patient_id}",
                    "encounter": f"Encounter/{satusehat_encounter_id}",
                    "code": "19005-8"
                })
                if res and res.get("total", 0) > 0:
                    observation_id = res["entry"][0]["resource"]["id"]
                    logger.info(f"Ditemukan existing Observation ID: {observation_id}")
                    log_integration_attempt(accession_number, "Observation", "POST", "SUCCESS", observation_payload, {"id": observation_id, "note": "Resolved duplicate via lookup"})
                else:
                    raise obs_err
            else:
                raise obs_err
        
        # 4. Buat Resource DiagnosticReport
        report_payload = {
            "resourceType": "DiagnosticReport",
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                            "code": "RAD",
                            "display": "Radiology"
                        }
                    ]
                }
            ],
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": "24606-6",
                        "display": f"Radiology study of {order_data['procedure_name']}"
                    }
                ]
            },
            "subject": {
                "reference": f"Patient/{satusehat_patient_id}",
                "display": order_data["patient_name"]
            },
            "encounter": {
                "reference": f"Encounter/{satusehat_encounter_id}"
            },
            "basedOn": [
                {
                    "reference": f"ServiceRequest/{order_data['servicerequest_id']}"
                }
            ],
            # Hubungkan ke ImagingStudy jika ada
            "imagingStudy": [
                {
                    "reference": f"ImagingStudy/{order_data['imagingstudy_id']}"
                }
            ] if order_data["imagingstudy_id"] else [],
            "result": [
                {
                    "reference": f"Observation/{observation_id}"
                }
            ],
            "resultsInterpreter": [
                {
                    "reference": f"Practitioner/{performer_doctor_id}",
                    "display": "dr. SOSOR TUAH I.P.T, Sp.Rad"
                }
            ],
            "performer": [
                {
                    "reference": f"Practitioner/{performer_doctor_id}",
                    "display": "dr. SOSOR TUAH I.P.T, Sp.Rad"
                }
            ],
            "conclusion": hasil_ekspertise
        }

        report_id = None
        try:
            report_res = satusehat_client.post_resource("DiagnosticReport", report_payload)
            report_id = report_res["id"]
        except Exception as rep_err:
            is_duplicate = False
            if hasattr(rep_err, "response") and rep_err.response is not None:
                try:
                    body = rep_err.response.json()
                    if "duplicate" in str(body).lower() or "20002" in str(body):
                        is_duplicate = True
                except Exception:
                    pass
            if "duplicate" in str(rep_err).lower() or "20002" in str(rep_err):
                is_duplicate = True
                
            if is_duplicate:
                logger.info(f"Mendeteksi duplicate DiagnosticReport untuk {accession_number}. Melakukan lookup...")
                try:
                    res = satusehat_client.search_resource("DiagnosticReport", {
                        "subject": f"Patient/{satusehat_patient_id}",
                        "encounter": f"Encounter/{satusehat_encounter_id}"
                    })
                    target_ref = f"ServiceRequest/{order_data['servicerequest_id']}"
                    found_id = None
                    if res and res.get("total", 0) > 0:
                        for entry in res.get("entry", []):
                            resource = entry.get("resource", {})
                            based_on_list = resource.get("basedOn", [])
                            for bo in based_on_list:
                                if bo.get("reference") == target_ref:
                                    found_id = resource.get("id")
                                    break
                            if found_id:
                                break
                    if found_id:
                        report_id = found_id
                        logger.info(f"Ditemukan existing DiagnosticReport ID: {report_id}")
                        report_res = {"id": report_id, "note": "Resolved duplicate via lookup"}
                    else:
                        raise rep_err
                except Exception as lookup_err:
                    logger.error(f"Gagal melakukan safe lookup DiagnosticReport: {lookup_err}")
                    raise rep_err
            else:
                raise rep_err
        
        # 5. Update Status Laporan lokal di database
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        cursor.execute(
            """
            UPDATE radiology_orders 
            SET satusehat_report_status = 'SENT', updated_at = GETDATE() 
            WHERE accession_number = ?
            """,
            (accession_number,)
        )
        bridge_conn.commit()
        bridge_conn.close()

        logger.info(f"DiagnosticReport berhasil didaftarkan ke SATUSEHAT: ID={report_id} untuk Accession={accession_number}")
        log_integration_attempt(accession_number, "DiagnosticReport", "POST", "SUCCESS", report_payload, report_res)

        # 6. Webhook callback: notifikasi SUKSES ke SIMRS
        if webhook_notifier.is_configured:
            try:
                webhook_notifier.send_notification(
                    accession_number=accession_number,
                    status="SUCCESS",
                    message=f"DiagnosticReport (ID={report_id}) dan Observation (ID={observation_id}) berhasil dikirim ke SATUSEHAT"
                )
                log_integration_attempt(accession_number, "Webhook", "POST", "SENT",
                                        {"accessionNumber": accession_number, "status": "SUCCESS"}, None)
            except Exception as wh_err:
                logger.error(f"Gagal memproses webhook: {wh_err}")

    except Exception as e:
        logger.error(f"Gagal mengirim hasil ekspertise {accession_number} ke SATUSEHAT: {e}")
        log_integration_attempt(accession_number, "DiagnosticReport", "POST", "FAILED", None, None, str(e))
        
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        cursor.execute(
            "UPDATE radiology_orders SET satusehat_report_status = 'FAILED', updated_at = GETDATE() WHERE accession_number = ?",
            (accession_number,)
        )
        bridge_conn.commit()
        bridge_conn.close()

        # Webhook callback: notifikasi GAGAL ke SIMRS
        if webhook_notifier.is_configured:
            try:
                webhook_notifier.send_notification(
                    accession_number=accession_number,
                    status="FAILED",
                    error_code="REPORT_SEND_FAILED",
                    message=f"Gagal mengirim DiagnosticReport ke SATUSEHAT: {str(e)}"
                )
                log_integration_attempt(accession_number, "Webhook", "POST", "SENT",
                                        {"accessionNumber": accession_number, "status": "FAILED"}, None)
            except Exception as wh_err:
                logger.error(f"Gagal memproses webhook: {wh_err}")
