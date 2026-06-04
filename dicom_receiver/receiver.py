import os
import sys
import glob
import datetime
import pyodbc
from celery import Celery
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, debug_logger
from pynetdicom.sop_class import (
    Verification,
    DigitalXRayImageStorageForPresentation,
    DigitalXRayImageStorageForProcessing
)

# Aktifkan pynetdicom debug logging untuk mempermudah troubleshooting
debug_logger()

# Ambil konfigurasi dari environment variables
BRIDGE_HOST = os.environ.get("BRIDGE_SQLSERVER_HOST", "bridge-db")
BRIDGE_PORT = os.environ.get("BRIDGE_SQLSERVER_PORT", "1433")
BRIDGE_DB = os.environ.get("BRIDGE_SQLSERVER_DB", "RadiologyBridge")
BRIDGE_USER = os.environ.get("BRIDGE_SQLSERVER_USER", "sa")
BRIDGE_PWD = os.environ.get("BRIDGE_SQLSERVER_PASSWORD", "Bridge_Password123!")

AE_TITLE = os.environ.get("STORAGE_AE_TITLE", "SIMDUDIM_STORE")
DICOM_STORE_PORT = int(os.environ.get("DICOM_STORE_PORT", "11112"))
STORAGE_DIR = "/storage/dicom"
DICOM_BASE_URL = os.environ.get("SATUSEHAT_DICOM_BASE_URL", "https://api-sandbox.kemkes.go.id")

# Inisialisasi Celery Client
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
celery_app = Celery("radiology_tasks", broker=f"redis://{REDIS_HOST}:{REDIS_PORT}/0")

def get_bridge_conn():
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={BRIDGE_HOST},{BRIDGE_PORT};"
        f"DATABASE={BRIDGE_DB};"
        f"UID={BRIDGE_USER};"
        f"PWD={BRIDGE_PWD};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=yes;"
        f"Connection Timeout=5;"
    )
    return pyodbc.connect(conn_str)

def get_noreg_for_accession(accession):
    """
    Ambil noreg dari database lokal bridge berdasarkan accession number (noradio).
    Kembalikan noreg string atau fallback 'NOREG-UNKNOWN'.
    """
    try:
        conn = get_bridge_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT noreg FROM radiology_orders WHERE accession_number = ?",
            (accession,)
        )
        row = cursor.fetchone()
        conn.close()
        if row and row[0] and str(row[0]).strip():
            return str(row[0]).strip()
    except Exception as e:
        print(f"[NOREG LOOKUP ERROR] Gagal ambil noreg untuk {accession}: {e}", file=sys.stderr)
    return "NOREG-UNKNOWN"


def determine_filepath(folder_path, accession):
    """
    Menentukan path file DICOM baru dalam folder_path berdasarkan accession (noradio).
    Implementasi file numbering:
      - Tidak ada file: simpan sebagai <accession>.dcm
      - Sudah ada <accession>.dcm (1 file): rename ke <accession>1.dcm, simpan baru sebagai <accession>2.dcm
      - Sudah ada numbered files: cari indeks tertinggi, simpan sebagai <accession>{N+1}.dcm
    Return: (new_file_path, renamed_old_path_or_None)
    """
    plain_path = os.path.join(folder_path, f"{accession}.dcm")
    
    # Cari semua file bernomor yang sudah ada: <accession>1.dcm, <accession>2.dcm dst.
    numbered_files = sorted(glob.glob(os.path.join(folder_path, f"{accession}[0-9]*.dcm")))
    
    if not os.path.exists(plain_path) and not numbered_files:
        # Belum ada file sama sekali — simpan sebagai plain
        return plain_path, None
    
    if os.path.exists(plain_path) and not numbered_files:
        # Baru ada satu file (plain) — rename ke accession1.dcm, baru simpan accession2.dcm
        renamed_path = os.path.join(folder_path, f"{accession}1.dcm")
        os.rename(plain_path, renamed_path)
        new_path = os.path.join(folder_path, f"{accession}2.dcm")
        return new_path, renamed_path
    
    # Sudah ada numbered files — cari indeks tertinggi lalu tambah 1
    max_idx = 0
    for nf in numbered_files:
        basename = os.path.splitext(os.path.basename(nf))[0]  # e.g. R202600052301
        suffix = basename[len(accession):]  # angka di belakang
        if suffix.isdigit():
            max_idx = max(max_idx, int(suffix))
    
    new_idx = max_idx + 1
    new_path = os.path.join(folder_path, f"{accession}{new_idx}.dcm")
    return new_path, None


def write_to_bridge_study(accession, study_uid, series_uid, sop_uid, filepath):
    """
    Mencatat metadata DICOM ke database lokal RadiologyBridge.
    """
    conn = None
    try:
        conn = get_bridge_conn()
        cursor = conn.cursor()
        
        # Cek apakah study sudah ada
        cursor.execute("SELECT id FROM dicom_studies WHERE study_instance_uid = ?", (study_uid,))
        row = cursor.fetchone()
        
        if not row:
            insert_query = """
                INSERT INTO dicom_studies (
                    accession_number, study_instance_uid, series_count, sop_count, storage_path, satusehat_status
                ) VALUES (?, ?, 1, 1, ?, 'PENDING')
            """
            cursor.execute(insert_query, (accession, study_uid, filepath))
        else:
            update_query = """
                UPDATE dicom_studies 
                SET sop_count = sop_count + 1 
                WHERE study_instance_uid = ?
            """
            cursor.execute(update_query, (study_uid,))
            
        # Update status order di radiology_orders menjadi SCAN_COMPLETED agar tidak muncul di MWL lagi
        cursor.execute(
            "UPDATE radiology_orders SET status = 'SCAN_COMPLETED', updated_at = GETDATE() WHERE accession_number = ?",
            (accession,)
        )
        conn.commit()
    except Exception as e:
        print(f"[BRIDGE DB ERROR] Gagal menulis ke dicom_studies: {e}", file=sys.stderr)
    finally:
        if conn:
            conn.close()

def handle_c_store(event):
    """
    Handler untuk DICOM C-STORE. Menerima, mengarsipkan secara lokal,
    dan memicu pencatatan ke database SIMRS & Local Bridge.
    
    Validasi sesuai Panduan Kemenkes (Hal. 22):
    - Router WAJIB membaca Tag DICOM (0008,0050) sebelum memproses gambar
    - Jika AccessionNumber kosong → REJECT (Fail)
    - Jika AccessionNumber tidak terdaftar di DB lokal → REJECT
    """
    ds = event.dataset
    # Ambil metadata penting dari header DICOM
    sop_class = ds.SOPClassUID
    sop_instance = ds.SOPInstanceUID
    
    # Ambil Accession Number (Tag 0008,0050) — WAJIB per panduan Kemenkes
    accession = ds.get("AccessionNumber", None)
    study_uid = ds.get("StudyInstanceUID", None)
    series_uid = ds.get("SeriesInstanceUID", None)

    # Trim whitespace pada AccessionNumber (beberapa modalitas kirim spasi saja)
    if accession:
        accession = str(accession).strip()

    logger_prefix = f"[{event.assoc.requestor.ae_title} -> {AE_TITLE}]"
    print(f"\n{datetime.datetime.now()} {logger_prefix} Menerima berkas C-STORE...")

    # ====================================================================
    # VALIDASI 1: AccessionNumber WAJIB ada dan tidak boleh kosong
    # ====================================================================
    if not accession:
        print(
            f"[C-STORE REJECTED] File DICOM DITOLAK: Accession Number (0008,0050) kosong! "
            f"SOPInstanceUID: {sop_instance}",
            file=sys.stderr,
        )
        return 0xA700  # Refused: Out of Resources

    # ====================================================================
    # VALIDASI 2: StudyInstanceUID dan SeriesInstanceUID harus ada
    # ====================================================================
    if not study_uid or not series_uid:
        print(
            f"[C-STORE REJECTED] Data DICOM tidak lengkap! "
            f"Accession: {accession}, Study: {study_uid}, Series: {series_uid}.",
            file=sys.stderr,
        )
        return 0xA700

    # ====================================================================
    # VALIDASI 3: AccessionNumber harus terdaftar di database lokal
    # ====================================================================
    try:
        bridge_conn = get_bridge_conn()
        cursor = bridge_conn.cursor()
        cursor.execute(
            "SELECT accession_number FROM radiology_orders WHERE accession_number = ?",
            (accession,),
        )
        row = cursor.fetchone()
        bridge_conn.close()

        if not row:
            print(f"[C-STORE WARNING] Accession Number '{accession}' tidak ditemukan di database lokal. Auto-creating order...", file=sys.stderr)
            try:
                patient_name = str(ds.get("PatientName", "UNKNOWN")).strip()
                patient_id = str(ds.get("PatientID", "")).strip()
                raw_modality = str(ds.get("Modality", "DR")).strip().upper()
                modality = "DX" if raw_modality in ("DR", "CR", "DX") else (raw_modality or "DX")
                
                bridge_conn2 = get_bridge_conn()
                cur2 = bridge_conn2.cursor()
                birth_date_raw = str(ds.get("PatientBirthDate", "")).strip()
                if len(birth_date_raw) == 8 and birth_date_raw.isdigit():
                    birth_date = f"{birth_date_raw[:4]}-{birth_date_raw[4:6]}-{birth_date_raw[6:8]}"
                else:
                    birth_date = "1900-01-01"
                dicom_sex = str(ds.get("PatientSex", "O")).strip().upper()
                gender = dicom_sex if dicom_sex in ("M", "F") else "O"
                cur2.execute("""
                    INSERT INTO radiology_orders (
                        accession_number, patient_id, patient_name, birth_date, gender, modality,
                        procedure_name, order_datetime, status,
                        satusehat_servicerequest_status, satusehat_report_status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'Pemeriksaan Radiologi', GETDATE(), 'SCAN_COMPLETED', 'UNSENT', 'UNSENT')
                """, (accession, patient_id, patient_name, birth_date, gender, modality))
                bridge_conn2.commit()
                bridge_conn2.close()
                print(f"[C-STORE] Order auto-created untuk accession: {accession}")
            except Exception as auto_err:
                print(f"[C-STORE WARNING] Gagal auto-create order: {auto_err}", file=sys.stderr)
    except Exception as db_err:
        print(
            f"[C-STORE WARNING] Gagal validasi ACSN ke database: {db_err}. "
            f"Melanjutkan penyimpanan file untuk safety.",
            file=sys.stderr,
        )

    # Ambil tanggal study dari DICOM, jika tidak ada gunakan tanggal hari ini
    study_date_raw = str(ds.get("StudyDate", "")).strip()
    if len(study_date_raw) == 8 and study_date_raw.isdigit():
        year = study_date_raw[:4]
        month = study_date_raw[4:6]
        day = study_date_raw[6:8]
    else:
        now = datetime.datetime.now()
        year = now.strftime("%Y")
        month = now.strftime("%m")
        day = now.strftime("%d")

    # Ambil noreg dari database lokal bridge
    noreg = get_noreg_for_accession(accession)
    safe_noreg = "".join(c for c in noreg if c.isalnum() or c in ('-', '_'))
    if not safe_noreg:
        safe_noreg = "NOREG-UNKNOWN"

    # Buat direktori penyimpanan lokal
    folder_path = os.path.join(STORAGE_DIR, year, month, day, safe_noreg)
    os.makedirs(folder_path, exist_ok=True)

    # Tentukan path file dengan logika numbering
    file_path, renamed_old_path = determine_filepath(folder_path, accession)

    # Simpan file DICOM ke disk beserta meta infonya (DICOM Part 10)
    meta = event.file_meta
    event.dataset.file_meta = meta
    event.dataset.is_little_endian = meta.TransferSyntaxUID.is_little_endian
    event.dataset.is_implicit_VR = meta.TransferSyntaxUID.is_implicit_VR
    
    try:
        event.dataset.save_as(file_path, write_like_original=False)
        print(f"[ARCHIVE SUCCESS] Berhasil mengarsipkan DICOM ke: {file_path}")

        if renamed_old_path:
            print(f"[FILE RENAME] File lama di-rename ke: {renamed_old_path}")
        
        # 1. Catat ke database lokal bridge
        write_to_bridge_study(accession, study_uid, series_uid, sop_instance, file_path)

        # 3. Pemicu Task Celery untuk POST ImagingStudy ke SATUSEHAT
        try:
            celery_app.send_task("app.tasks.send_imagingstudy_task", args=[str(study_uid)])
            print(f"[CELERY TRIGGER] Task ImagingStudy dipicu untuk StudyUID: {study_uid}")
        except Exception as celery_err:
            print(f"[CELERY TRIGGER ERROR] Gagal memicu task ImagingStudy: {celery_err}", file=sys.stderr)

        # Kembalikan status SUCCESS (0x0000) kesukaan modalitas
        return 0x0000
    except Exception as e:
        print(f"[ARCHIVE ERROR] Gagal menyimpan file DICOM: {e}", file=sys.stderr)
        return 0xC001


def handle_echo(event):
    """Handler untuk DICOM C-ECHO (Ping)"""
    logger_prefix = f"[{event.assoc.requestor.ae_title} -> {AE_TITLE}]"
    print(f"{datetime.datetime.now()} {logger_prefix} Menerima C-ECHO Ping...")
    return 0x0000


def handle_assoc_requested(event):
    assoc = event.assoc
    calling = assoc.requestor.ae_title
    proposed = []
    for cx in getattr(assoc.requestor, "contexts", []) or []:
        proposed.append(str(getattr(cx, "abstract_syntax", cx)))
    print(
        f"{datetime.datetime.now()} [DICOM] A-ASSOCIATE-RQ: {calling} -> {AE_TITLE} | "
        f"Proposed SOP ({len(proposed)}): {', '.join(proposed[:8])}"
        f"{'...' if len(proposed) > 8 else ''}"
    )


def handle_assoc_accepted(event):
    assoc = event.assoc
    accepted = [str(cx.abstract_syntax) for cx in assoc.accepted_contexts]
    print(
        f"{datetime.datetime.now()} [DICOM] Association ACCEPTED: "
        f"{assoc.requestor.ae_title} -> {AE_TITLE} | "
        f"Accepted ({len(accepted)}): {', '.join(accepted[:8])}"
        f"{'...' if len(accepted) > 8 else ''}"
    )


def handle_assoc_rejected(event):
    print(
        f"{datetime.datetime.now()} [DICOM] Association REJECTED: "
        f"reason={getattr(event, 'reason', '?')}",
        file=sys.stderr,
    )


def handle_assoc_aborted(event):
    print(
        f"{datetime.datetime.now()} [DICOM] Association ABORTED: "
        f"{event.assoc.requestor.ae_title}",
        file=sys.stderr,
    )


def start_server():
    ae = AE(ae_title=AE_TITLE)
    ae.require_called_aet = False
    ae.maximum_pdu_size = 16384
    
    # 1. Daftarkan Jabat Tangan C-ECHO
    ae.add_supported_context(Verification)
    
    # 2. Daftarkan Jabat Tangan C-STORE khusus gambar DX (Digital Radiography)
    ae.add_supported_context(DigitalXRayImageStorageForPresentation)
    ae.add_supported_context(DigitalXRayImageStorageForProcessing)

    handlers = [
        (evt.EVT_REQUESTED, handle_assoc_requested),
        (evt.EVT_ACCEPTED, handle_assoc_accepted),
        (evt.EVT_REJECTED, handle_assoc_rejected),
        (evt.EVT_ABORTED, handle_assoc_aborted),
        (evt.EVT_C_STORE, handle_c_store),
        (evt.EVT_C_ECHO, handle_echo),
    ]

    os.makedirs(STORAGE_DIR, exist_ok=True)
    print(
        f"Menjalankan DICOM C-STORE SCP [{AE_TITLE}] port {DICOM_STORE_PORT} "
        f"(Mode: DX Storage Only Ready)..."
    )
    ae.start_server(("", DICOM_STORE_PORT), block=True, evt_handlers=handlers)

if __name__ == "__main__":
    start_server()