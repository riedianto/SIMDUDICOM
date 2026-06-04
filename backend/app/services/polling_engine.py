import time
import uuid
import datetime
from app.database.connection import get_simrs_conn, get_bridge_conn
from app.core.logging_config import logger
from app.core.config import settings

def generate_accession_number():
    """
    Menghasilkan Accession Number unik jika dari SIMRS kosong.
    Format: RAD-YYYYMMDD-XXXX (4 digit random)
    """
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    unique_suffix = str(uuid.uuid4().int)[:4]
    return f"RAD-{date_str}-{unique_suffix}"

def determine_modality(procedure_name: str) -> str:
    """
    Menentukan modality (CT, MR, US, CR, dll) berdasarkan nama tindakan.
    """
    proc_upper = procedure_name.upper()
    if "CT" in proc_upper or "MSCT" in proc_upper:
        return "CT"
    elif "MRI" in proc_upper or "MAGNETIC" in proc_upper:
        return "MR"
    elif "USG" in proc_upper or "ULTRASOUND" in proc_upper or "ULTRA" in proc_upper:
        return "US"
    elif "MAMMO" in proc_upper:
        return "MG"
    elif "PANORAMIC" in proc_upper or "DENTAL" in proc_upper:
        return "DX"
    elif "CR" in proc_upper or "DR" in proc_upper or "FOTO" in proc_upper or "X-RAY" in proc_upper or "THORAX" in proc_upper or "XRAY" in proc_upper:
        return "DX"
    else:
        return "DX"  # Default fallback modality


def normalize_modality(code: str) -> str:
    """Standarisasi kode modality DICOM; plain X-ray selalu DX (bukan DR/CR)."""
    c = (code or "").strip().upper()
    if c in ("DR", "CR", "DX"):
        return "DX"
    return c or "DX"


def poll_new_orders():
    """
    Membaca order radiologi baru dari database SIMRS (artha_medika)
    dan menyimpannya ke database lokal bridge (RadiologyBridge).
    """
    simrs_conn = None
    bridge_conn = None
    try:
        simrs_conn = get_simrs_conn()
        bridge_conn = get_bridge_conn()
        
        simrs_cursor = simrs_conn.cursor()
        bridge_cursor = bridge_conn.cursor()

        # 1. Ambil Accession Number yang sudah ada di lokal
        bridge_cursor.execute("SELECT accession_number FROM radiology_orders")
        local_accessions = {row[0] for row in bridge_cursor.fetchall()}

        # 2. Query SIMRS: ambil order dari 1 hari terakhir beserta detail tindakannya
        query_simrs = """
            SELECT 
                hr.noradio,
                hr.rekmed,
                hr.namapas,
                hr.tgllahir,
                hr.jkel,
                dk.nadokter,
                hr.tglradio,
                t.tindakan,
                hr.noreg
            FROM 
                tbl_hradio hr WITH (NOLOCK)
            LEFT JOIN 
                tbl_dokter dk WITH (NOLOCK) ON hr.drperiksa = dk.kodokter
            LEFT JOIN
                tbl_dradio dr WITH (NOLOCK) ON hr.noradio = dr.noradio
            LEFT JOIN
                tbl_tarif t WITH (NOLOCK) ON dr.kodetarif = t.kodetarif
            WHERE 
                hr.tglradio >= DATEADD(day, -1, GETDATE())
        """
        simrs_cursor.execute(query_simrs)
        rows = simrs_cursor.fetchall()

        orders_dict = {}
        for row in rows:
            noradio = row[0]
            rekmed = str(row[1]).strip() if row[1] is not None else ""
            namapas = row[2]
            tgllahir = row[3]
            jkel = str(row[4]).strip() if row[4] is not None else "0"
            nadokter = row[5] or "Dokter Tidak Diketahui"
            tglinput = row[6]
            tindakan = str(row[7]).strip() if row[7] is not None else "Pemeriksaan Radiologi"
            noreg = str(row[8]).strip() if row[8] is not None else ""

            # Generate accession number jika kosong
            accession_number = noradio
            if not accession_number or str(accession_number).strip() == "":
                accession_number = generate_accession_number()
                logger.info(f"Accession number kosong, generated: {accession_number}")

            if accession_number in local_accessions:
                continue

            if accession_number not in orders_dict:
                orders_dict[accession_number] = {
                    "rekmed": rekmed,
                    "namapas": namapas,
                    "tgllahir": tgllahir,
                    "jkel": jkel,
                    "nadokter": nadokter,
                    "tglinput": tglinput,
                    "noreg": noreg,
                    "procedures": []
                }
            if tindakan not in orders_dict[accession_number]["procedures"]:
                orders_dict[accession_number]["procedures"].append(tindakan)

        new_orders_count = 0
        for accession_number, order_info in orders_dict.items():
            procedure_name = ", ".join(order_info["procedures"])
            if len(procedure_name) > 255:
                procedure_name = procedure_name[:252] + "..."

            modality = normalize_modality(determine_modality(procedure_name))

            # Map jenis kelamin
            gender = "O"
            if order_info["jkel"] == "1":
                gender = "M"
            elif order_info["jkel"] == "2":
                gender = "F"

            # Insert ke database lokal bridge
            insert_query = """
                INSERT INTO radiology_orders (
                    accession_number, patient_id, patient_name, birth_date, 
                    gender, doctor_name, order_datetime, status, 
                    modality, procedure_name, noreg,
                    satusehat_servicerequest_status, satusehat_report_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, 'UNSENT', 'UNSENT')
            """
            bridge_cursor.execute(
                insert_query, 
                (accession_number, order_info["rekmed"], order_info["namapas"], order_info["tgllahir"], 
                 gender, order_info["nadokter"], order_info["tglinput"], modality, procedure_name,
                 order_info["noreg"])
            )
            bridge_conn.commit()
            new_orders_count += 1
            logger.info(f"Order baru terdeteksi & disimpan: {accession_number} - Pasien: {order_info['namapas']} - Modality: {modality} - Proc: {procedure_name}")
            
            # Memicu Task SATUSEHAT POST ServiceRequest
            try:
                from app.tasks import send_servicerequest_task
                send_servicerequest_task.delay(accession_number)
                logger.info(f"Task Celery ServiceRequest dipicu untuk: {accession_number}")
            except Exception as task_err:
                logger.error(f"Gagal memicu Task Celery ServiceRequest untuk {accession_number}: {task_err}")

        if new_orders_count > 0:
            logger.info(f"Berhasil mensinkronisasi {new_orders_count} order radiologi baru.")

    except Exception as e:
        logger.error(f"Error saat menjalankan polling order baru: {e}")
    finally:
        if simrs_conn:
            simrs_conn.close()
        if bridge_conn:
            bridge_conn.close()

def check_completed_examinations():
    """
    Memeriksa apakah dokter sudah mengisi hasil ekspertise di SIMRS
    untuk mengubah status pemeriksaan lokal menjadi COMPLETED.
    """
    simrs_conn = None
    bridge_conn = None
    try:
        bridge_conn = get_bridge_conn()
        bridge_cursor = bridge_conn.cursor()

        # Ambil order lokal yang statusnya masih PENDING, SCHEDULED, atau SCAN_COMPLETED
        bridge_cursor.execute(
            "SELECT accession_number, satusehat_report_status FROM radiology_orders WHERE status IN ('PENDING', 'SCHEDULED', 'SCAN_COMPLETED')"
        )
        active_orders = [(row[0], str(row[1]).strip() if row[1] else "UNSENT") for row in bridge_cursor.fetchall()]

        if not active_orders:
            return

        simrs_conn = get_simrs_conn()
        simrs_cursor = simrs_conn.cursor()

        completed_count = 0
        for accession, report_status in active_orders:
            # Query ke SIMRS untuk memeriksa apakah hasil ekspertise/bacaan sudah diisi
            # Menggunakan table tbl_radioexpert dengan kolom expertise
            query = """
                SELECT expertise FROM tbl_radioexpert WITH (NOLOCK) 
                WHERE noradio = ? AND expertise IS NOT NULL AND DATALENGTH(expertise) > 0
            """
            simrs_cursor.execute(query, (accession,))
            row = simrs_cursor.fetchone()
            
            if row:
                # Update status di database lokal bridge menjadi COMPLETED
                update_query = """
                    UPDATE radiology_orders 
                    SET status = 'COMPLETED', updated_at = GETDATE() 
                    WHERE accession_number = ?
                """
                bridge_cursor.execute(update_query, (accession,))
                bridge_conn.commit()
                completed_count += 1
                logger.info(f"Hasil ekspertise terdeteksi di SIMRS untuk order: {accession}. Status diupdate ke COMPLETED.")

                # Hanya trigger send_report_task jika belum pernah berhasil dikirim
                if report_status == "SENT":
                    logger.info(f"Laporan untuk {accession} sudah pernah dikirim (SENT). Skip trigger ulang.")
                    continue

                # Memicu Task SATUSEHAT POST Observation & DiagnosticReport
                try:
                    from app.tasks import send_report_task
                    send_report_task.delay(accession)
                    logger.info(f"Task Celery DiagnosticReport dipicu untuk: {accession}")
                except Exception as task_err:
                    logger.error(f"Gagal memicu Task Celery DiagnosticReport untuk {accession}: {task_err}")

        if completed_count > 0:
            logger.info(f"Berhasil memperbarui {completed_count} order menjadi COMPLETED.")

    except Exception as e:
        logger.error(f"Error saat mengecek ekspertise pemeriksaan: {e}")
    finally:
        if simrs_conn:
            simrs_conn.close()
        if bridge_conn:
            bridge_conn.close()

def start_polling_loop():
    """
    Loop utama polling engine yang berjalan setiap 5 detik
    """
    logger.info("Memulai SQL Polling Engine Loop (Setiap 5 detik)...")
    while True:
        poll_new_orders()
        check_completed_examinations()
        time.sleep(5)

if __name__ == "__main__":
    from app.core.logging_config import setup_logging
    setup_logging()
    start_polling_loop()
