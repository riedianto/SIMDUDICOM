import os
import sys
import datetime
import pyodbc
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence
from pynetdicom import AE, evt, debug_logger
from pynetdicom.sop_class import ModalityWorklistInformationFind, Verification

# Aktifkan pynetdicom debug logging untuk mempermudah troubleshooting
debug_logger()

# Ambil konfigurasi dari environment variables
DB_HOST = os.environ.get("BRIDGE_SQLSERVER_HOST", "bridge-db")
DB_PORT = os.environ.get("BRIDGE_SQLSERVER_PORT", "1433")
DB_DB = os.environ.get("BRIDGE_SQLSERVER_DB", "RadiologyBridge")
DB_USER = os.environ.get("BRIDGE_SQLSERVER_USER", "sa")
DB_PWD = os.environ.get("BRIDGE_SQLSERVER_PASSWORD", "Bridge_Password123!")
AE_TITLE = os.environ.get("MWL_AE_TITLE", "SIMDUDIM")

XRAY_MODALITIES = frozenset({"DR", "CR", "DX"})


def pad_dicom_pn(value: str, width: int = 32) -> str:
    """Padding PN seperti Orthanc: 1 kata -> pad ke 32; multi-kata -> +8 caret setelah suffix."""
    if not value:
        return "^" * width
    if len(value) >= width and "," not in value:
        return value[:width]
    if "," in value:
        base = value  # sudah include suffix NY/TN
        main = value.split(",", 1)[0]
        word_count = main.count("^") + 1
        if word_count <= 2:
            return base + ("^" * max(0, width - len(base)))
        # Orthanc 3+ kata: SAHRA^MITHA^WAHYUNI,NY^^^^^^^^
        return base + ("^" * 8)
    return value + ("^" * max(0, width - len(value)))


def format_simrs_physician_name(name: str, max_component_len: int = 64) -> str:
    """Nama dokter perujuk: pertahankan prefix DR. seperti worklist Orthanc lama."""
    if not name:
        return ""
    text = " ".join(str(name).strip().split()).upper()
    if not text.startswith("DR"):
        text = f"DR. {text}" if not text.startswith("DR.") else text
    elif text.startswith("DR ") and not text.startswith("DR. "):
        text = "DR. " + text[3:]

    if "," in text:
        name_part, suffix_part = text.split(",", 1)
        words = name_part.split()
        if len(words) <= 1:
            formatted = words[0][:max_component_len] if words else ""
        else:
            formatted = "^".join(w[:max_component_len] for w in words)
        suffix_part = suffix_part.strip()[:max_component_len]
        result = f"{formatted},{suffix_part}" if suffix_part else formatted
    else:
        words = text.split()
        result = words[0][:max_component_len] if len(words) == 1 else "^".join(w[:max_component_len] for w in words)

    # Orthanc/XMARUM: kredensial setelah koma diawali ^  -> DR.^SOSOR^TUAH^I.P.T,^SP.RAD
    if "," in result and not result.split(",", 1)[1].startswith("^"):
        main, cred = result.split(",", 1)
        result = f"{main},^{cred.lstrip('^')}"

    return pad_dicom_pn(result, 64)


def mwl_response_modality(stored_modality: str) -> str:
    """Modality di respons worklist: plain X-ray selalu DX (standar DICOM)."""
    raw = (stored_modality or "").strip().upper()
    if raw in XRAY_MODALITIES:
        return "DX"
    return raw or "DX"


def build_study_instance_uid(accession_number: str) -> str:
    """
    Mengembalikan Study Instance UID untuk worklist MWL.
    Prioritas:
    1. Jika alat sudah pernah kirim C-STORE → gunakan UID asli dari alat (dari dicom_studies).
    2. Jika belum ada → generate UID valid menggunakan OID organisasi RS.
       Format: 2.25.{integer-dari-accession-number} (ISO OID arc 2.25 = UUID-based, aman & unik)
    Catatan: prefix lama 1.2.840.10008.5.1.4.1.1.999 adalah reserved SOP Class UID,
    TIDAK boleh digunakan sebagai Study Instance UID.
    """
    # 1. Cek apakah sudah ada UID asli dari alat di database lokal
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT study_instance_uid FROM dicom_studies WHERE accession_number = ?",
            (accession_number,)
        )
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            uid = str(row[0]).strip()
            if uid:
                return uid
    except Exception as e:
        print(f"[MWL] Gagal lookup study_instance_uid dari DB untuk {accession_number}: {e}", file=sys.stderr)

    # 2. Generate UID deterministik menggunakan OID organisasi RS (100028327)
    # Format: 1.2.410.200067.100.1.<accession_digits>
    # 1.2.410.200067 = OID prefix Korea/vendor XMARUM-compatible
    # .100.1 = sub-arc organisasi RS
    # Sanitasi accession: ambil hanya digit, fallback ke hash jika tidak ada digit
    acc_digits = "".join(c for c in str(accession_number) if c.isdigit())
    if not acc_digits:
        # Fallback: gunakan hash integer dari string accession
        acc_digits = str(abs(hash(accession_number)) % (10 ** 15))
    return f"1.2.410.200067.100.1.{acc_digits}"


def get_db_conn():
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={DB_HOST},{DB_PORT};"
        f"DATABASE={DB_DB};"
        f"UID={DB_USER};"
        f"PWD={DB_PWD};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=yes;"
        f"Connection Timeout=5;"
    )
    return pyodbc.connect(conn_str)

def handle_c_find(event):
    """
    Handler untuk DICOM C-FIND MWL request.
    """
    request_ds = event.identifier
    logger_prefix = f"[{event.assoc.requestor.ae_title} -> {AE_TITLE}]"
    print(f"\n{datetime.datetime.now()} {logger_prefix} Menerima C-FIND Query...")
    
    # Audit log request keys
    # print(request_ds)

    # Ambil kunci pencarian jika dikirimkan oleh modality
    patient_id_query = ""
    patient_name_query = ""
    accession_query = ""
    modality_query = ""

    if 'PatientID' in request_ds and request_ds.PatientID:
        patient_id_query = str(request_ds.PatientID).replace("*", "%").strip()
    
    if 'PatientName' in request_ds and request_ds.PatientName:
        patient_name_query = str(request_ds.PatientName).replace("*", "%").strip()
        
    if 'AccessionNumber' in request_ds and request_ds.AccessionNumber:
        accession_query = str(request_ds.AccessionNumber).replace("*", "%").strip()

    # Periksa ScheduledProcedureStepSequence untuk filter Modality, Date, Station AE
    start_date_query = ""
    station_ae_query = ""
    if 'ScheduledProcedureStepSequence' in request_ds:
        spss = request_ds.ScheduledProcedureStepSequence[0]
        if 'Modality' in spss and spss.Modality:
            modality_query = str(spss.Modality).strip()
        if 'ScheduledProcedureStepStartDate' in spss and spss.ScheduledProcedureStepStartDate:
            start_date_query = str(spss.ScheduledProcedureStepStartDate).strip()
        if 'ScheduledStationAETitle' in spss and spss.ScheduledStationAETitle:
            station_ae_query = str(spss.ScheduledStationAETitle).strip()

    print(f"Kriteria Query - PatientID: '{patient_id_query}', PatientName: '{patient_name_query}', "
          f"Accession: '{accession_query}', Modality: '{modality_query}', Date: '{start_date_query}', "
          f"StationAE: '{station_ae_query}'")

    # Ambil data dari database RadiologyBridge SQL Server
    conn = None
    orders = []
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        # Bangun SQL query berdasarkan filter C-FIND
        sql_query = """
            SELECT 
                accession_number, patient_id, patient_name, birth_date, 
                gender, doctor_name, modality, procedure_name, order_datetime
            FROM 
                radiology_orders
            WHERE 
                status IN ('PENDING', 'SCHEDULED')
        """
        params = []
        
        if patient_id_query:
            sql_query += " AND patient_id LIKE ?"
            params.append(patient_id_query)
        if patient_name_query:
            sql_query += " AND patient_name LIKE ?"
            params.append(patient_name_query)
        if accession_query:
            sql_query += " AND accession_number LIKE ?"
            params.append(accession_query)
        if modality_query:
            if modality_query.upper() in XRAY_MODALITIES:
                sql_query += " AND modality IN ('DX', 'DR', 'CR')"
            else:
                sql_query += " AND modality = ?"
                params.append(modality_query)
        else:
            # XMARUM di mesin DR: default hanya tampilkan X-ray, bukan CT
            sql_query += " AND modality IN ('DX', 'DR', 'CR')"
        if start_date_query:
            # Format DICOM Date can be YYYYMMDD, YYYYMMDD-YYYYMMDD, YYYYMMDD-, or -YYYYMMDD
            if '-' in start_date_query:
                parts = start_date_query.split('-')
                start_part = parts[0].strip()
                end_part = parts[1].strip()
                
                if start_part:
                    try:
                        start_dt = f"{start_part[0:4]}-{start_part[4:6]}-{start_part[6:8]}"
                        sql_query += " AND order_datetime >= ?"
                        params.append(start_dt)
                    except Exception:
                        pass
                if end_part:
                    try:
                        end_dt = f"{end_part[0:4]}-{end_part[4:6]}-{end_part[6:8]} 23:59:59"
                        sql_query += " AND order_datetime <= ?"
                        params.append(end_dt)
                    except Exception:
                        pass
            else:
                # Single date YYYYMMDD
                try:
                    dt = f"{start_date_query[0:4]}-{start_date_query[4:6]}-{start_date_query[6:8]}"
                    sql_query += " AND CAST(order_datetime AS DATE) = ?"
                    params.append(dt)
                except Exception:
                    pass

        sql_query += " ORDER BY order_datetime ASC"

        cursor.execute(sql_query, params)
        rows = cursor.fetchall()
        
        for r in rows:
            orders.append({
                "accession_number": str(r[0]).strip(),
                "patient_id": str(r[1]).strip(),
                "patient_name": str(r[2]).strip() if r[2] else "",
                "birth_date": r[3],
                "gender": str(r[4]).strip() if r[4] else "O",
                "doctor_name": str(r[5]).strip() if r[5] else "",
                "modality": str(r[6]).strip() if r[6] else "DX",
                "procedure_name": str(r[7]).strip() if r[7] else "Pemeriksaan Radiologi",
                "order_datetime": r[8]
            })
            
        print(f"Ditemukan {len(orders)} order yang cocok di database lokal.")
        
    except Exception as e:
        print(f"Error query database lokal: {e}", file=sys.stderr)
    finally:
        if conn:
            conn.close()

    # Yield hasil ke Modality
    for order in orders:
        # Periksa pembatalan asosiasi oleh requestor
        if event.is_cancelled:
            yield 0xFE00, None
            return

        # Buat dataset respons DICOM
        ds = Dataset()
        ds.SpecificCharacterSet = 'ISO_IR 100'
        ds.AccessionNumber = order["accession_number"]
        # PatientID = rekmed (bukan noreg); noreg hanya di bridge DB / storage / SATUSEHAT
        ds.PatientID = order["patient_id"]
        
        # PatientName sama persis dengan Order Monitoring (tanpa reformat DICOM PN)
        ds.PatientName = order["patient_name"]
        
        # Format Birth Date (YYYYMMDD)
        if order["birth_date"]:
            ds.PatientBirthDate = order["birth_date"].strftime("%Y%m%d")
        else:
            ds.PatientBirthDate = ""
            
        ds.PatientSex = order["gender"]
        ds.ReferringPhysicianName = format_simrs_physician_name(order["doctor_name"])
        ds.StudyInstanceUID = build_study_instance_uid(order["accession_number"])

        # Buat Scheduled Procedure Step Sequence (0040,0100)
        sps_ds = Dataset()

        # XMARUM filter: Station AE harus match query (XMARUM), bukan MWL server AE (SIMDUDIM)
        sps_ds.ScheduledStationAETitle = (
            station_ae_query or event.assoc.requestor.ae_title or AE_TITLE
        )
        
        if order["order_datetime"]:
            sps_ds.ScheduledProcedureStepStartDate = order["order_datetime"].strftime("%Y%m%d")
            sps_ds.ScheduledProcedureStepStartTime = order["order_datetime"].strftime("%H%M%S")
        else:
            now = datetime.datetime.now()
            sps_ds.ScheduledProcedureStepStartDate = now.strftime("%Y%m%d")
            sps_ds.ScheduledProcedureStepStartTime = now.strftime("%H%M%S")
            
        sps_ds.Modality = mwl_response_modality(order["modality"])
        sps_ds.ScheduledPerformingPhysicianName = ""
        sps_ds.ScheduledProcedureStepDescription = order["procedure_name"]
        acc = order["accession_number"]
        sps_ds.ScheduledProcedureStepID = acc[:16]
        sps_ds.ScheduledStationName = "BRIDGE_STATION"
        sps_ds.ScheduledProcedureStepLocation = "RADIOLOGI_DEPT"
        
        ds.ScheduledProcedureStepSequence = Sequence([sps_ds])
        ds.RequestedProcedureID = acc[:16]
        ds.RequestedProcedureDescription = order["procedure_name"] or "Pemeriksaan Radiologi"

        print(f"-> Mengirim respons worklist: {order['accession_number']} | "
              f"PatientID={order['patient_id']} | PN={order['patient_name']}")
        
        # Status SUCCESS pending (0xFF00)
        yield 0xFF00, ds

    # Selesai
    yield 0x0000, None

def handle_echo(event):
    """
    Handler untuk DICOM C-ECHO (Ping)
    """
    logger_prefix = f"[{event.assoc.requestor.ae_title} -> {AE_TITLE}]"
    print(f"{datetime.datetime.now()} {logger_prefix} Menerima C-ECHO Ping...")
    return 0x0000

def start_server():
    ae = AE(ae_title=AE_TITLE)
    # Tambahkan SOP Class Modality Worklist Find dan Verification (C-ECHO)
    ae.add_supported_context(ModalityWorklistInformationFind)
    ae.add_supported_context(Verification)

    # Pasang event handler
    handlers = [
        (evt.EVT_C_FIND, handle_c_find),
        (evt.EVT_C_ECHO, handle_echo)
    ]

    print(f"Menjalankan DICOM MWL SCP Server [{AE_TITLE}] di port 104...")
    # Port 104 didalam docker, diproxy oleh NGINX dari port 104 host.
    ae.start_server(('', 104), block=True, evt_handlers=handlers)

if __name__ == "__main__":
    start_server()
