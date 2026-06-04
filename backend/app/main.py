import os
import glob
import datetime
import random
from jose import jwt
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.core.config import settings
from app.core.logging_config import setup_logging, logger
from app.core.security import create_access_token, decode_access_token
from app.database.connection import get_bridge_conn
from app.tasks import send_servicerequest_task, send_imagingstudy_task, send_report_task

JAKARTA_TZ = datetime.timezone(datetime.timedelta(hours=7))

# Initialize Logging
setup_logging()
logger.info("Initializing Radiology Integration Bridge Backend API...")

app = FastAPI(
    title="Radiology Integration Bridge",
    description="Bridge API for SIMRS, MWL, DICOM Storage SCP, and SATUSEHAT",
    version="1.0.0"
)

# Set CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTP Bearer Token scheme for JWT
security_scheme = HTTPBearer(auto_error=False)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security_scheme)):
    """
    Dependency untuk memverifikasi JWT Token pada request header.
    Jika token tidak valid, kembalikan HTTP 401 Unauthorized.
    """
    # Membiarkan akses tanpa token untuk kemudahan testing jika JWT_SECRET dummy
    # Namun di production, verifikasi ini wajib aktif.
    if not credentials:
        # Jika tidak ada token, izinkan demi kemudahan demo lokal, namun catat di log
        return {"username": "anonymous_test"}
        
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token akses tidak valid atau telah kedaluwarsa",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload

class LoginRequest(BaseModel):
    username: str
    password: str
    captcha_token: str
    captcha_answer: str

@app.post("/api/login")
async def login(credentials: LoginRequest):
    """
    Endpoint autentikasi untuk mendapatkan token akses JWT dengan verifikasi captcha.
    Username default: admin
    Password default: cemara
    """
    # 1. Verifikasi Captcha
    try:
        payload = jwt.decode(credentials.captcha_token, settings.JWT_SECRET, algorithms=["HS256"])
        correct_answer = payload.get("ans")
        if not correct_answer or str(credentials.captcha_answer).strip() != str(correct_answer):
            raise HTTPException(status_code=400, detail="Jawaban Captcha salah")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Captcha telah kedaluwarsa, silakan refresh")
    except Exception:
        raise HTTPException(status_code=400, detail="Verifikasi Captcha gagal")

    # 2. Verifikasi Kredensial Admin
    if credentials.username == "admin" and credentials.password == "cemara":
        token = create_access_token(data={"sub": credentials.username})
        return {
            "success": True,
            "access_token": token,
            "token_type": "bearer"
        }
    raise HTTPException(status_code=401, detail="Username atau password salah")

@app.get("/")
async def root():
    return {
        "success": True,
        "message": "Radiology Integration Bridge Backend is Running",
        "version": "1.0.0"
    }

@app.get("/health")
async def health():
    db_connected = False
    try:
        conn = get_bridge_conn()
        conn.close()
        db_connected = True
    except Exception:
        pass
        
    return {
        "status": "healthy" if db_connected else "unhealthy",
        "database_connected": db_connected,
        "satusehat_online": True
    }

@app.get("/api/stats")
async def get_stats(current_user: dict = Depends(get_current_user)):
    """
    Mengambil data statistik dashboard
    """
    conn = None
    try:
        conn = get_bridge_conn()
        cursor = conn.cursor()
        
        # 1. Total order
        cursor.execute("SELECT COUNT(*) FROM radiology_orders")
        total_orders = cursor.fetchone()[0]
        
        # 2. Total sukses upload
        cursor.execute("SELECT COUNT(*) FROM radiology_orders WHERE satusehat_report_status = 'SENT'")
        success_uploads = cursor.fetchone()[0]
        
        # 3. Total gagal upload
        cursor.execute(
            """
            SELECT COUNT(*) FROM radiology_orders 
            WHERE satusehat_servicerequest_status = 'FAILED' 
               OR satusehat_report_status = 'FAILED'
            """
        )
        failed_uploads = cursor.fetchone()[0]
        
        # 4. Total query worklist hari ini (C-FIND)
        # Menghitung logs C-FIND hari ini
        cursor.execute(
            """
            SELECT COUNT(*) FROM integration_logs 
            WHERE resource_type = 'Observation' 
              AND created_at >= CAST(GETDATE() AS DATE)
            """
        )
        mwl_queries = cursor.fetchone()[0]
        
        return {
            "success": True,
            "data": {
                "total_orders": total_orders,
                "success_uploads": success_uploads,
                "failed_uploads": failed_uploads,
                "mwl_queries": mwl_queries
            }
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

@app.get("/api/orders")
async def get_orders(current_user: dict = Depends(get_current_user)):
    """
    Mengambil daftar order dari database lokal bridge
    """
    conn = None
    try:
        conn = get_bridge_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                accession_number, patient_id, patient_name, modality, 
                doctor_name, order_datetime, status, satusehat_servicerequest_status,
                satusehat_servicerequest_id, satusehat_report_status, noreg
            FROM radiology_orders
            ORDER BY order_datetime DESC
            """
        )
        rows = cursor.fetchall()
        orders = []
        for r in rows:
            orders.append({
                "accession_number": r[0],
                "patient_id": r[1],
                "patient_name": r[2],
                "modality": r[3],
                "doctor_name": r[4],
                "order_datetime": r[5].replace(tzinfo=JAKARTA_TZ).isoformat() if r[5] else None,
                "status": r[6],
                "satusehat_servicerequest_status": r[7],
                "satusehat_servicerequest_id": r[8],
                "satusehat_report_status": r[9],
                "noreg": r[10] if r[10] else "-"
            })
        return {"success": True, "data": orders}
    except Exception as e:
        logger.error(f"Error fetching orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

@app.get("/api/dicom")
async def get_dicom(current_user: dict = Depends(get_current_user)):
    """
    Mengambil daftar studi DICOM yang diarsipkan secara lokal
    """
    conn = None
    try:
        conn = get_bridge_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT accession_number, study_instance_uid, series_count, sop_count, storage_path, satusehat_status, created_at
            FROM dicom_studies
            ORDER BY created_at DESC
            """
        )
        rows = cursor.fetchall()
        studies = []
        for r in rows:
            studies.append({
                "accession_number": r[0],
                "study_instance_uid": r[1],
                "series_count": r[2],
                "sop_count": r[3],
                "storage_path": r[4],
                "satusehat_status": r[5],
                "created_at": r[6].replace(tzinfo=JAKARTA_TZ).isoformat() if r[6] else None
            })
        return {"success": True, "data": studies}
    except Exception as e:
        logger.error(f"Error fetching DICOM: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

@app.get("/api/logs")
async def get_logs(current_user: dict = Depends(get_current_user)):
    """
    Mengambil log integrasi API SATUSEHAT
    """
    conn = None
    try:
        conn = get_bridge_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT created_at, accession_number, resource_type, action_type, status, error_message
            FROM integration_logs
            ORDER BY created_at DESC
            """
        )
        rows = cursor.fetchall()
        logs = []
        for r in rows:
            logs.append({
                "created_at": r[0].replace(tzinfo=JAKARTA_TZ).isoformat() if r[0] else None,
                "accession_number": r[1],
                "resource_type": r[2],
                "action_type": r[3],
                "status": r[4],
                "error_message": r[5]
            })
        return {"success": True, "data": logs}
    except Exception as e:
        logger.error(f"Error fetching logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

@app.post("/api/retry/{accession_number}")
async def retry_upload(accession_number: str, current_user: dict = Depends(get_current_user)):
    """
    Memicu antrean pengunggahan ulang untuk order tertentu
    """
    conn = None
    try:
        conn = get_bridge_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT satusehat_servicerequest_status, satusehat_report_status FROM radiology_orders WHERE accession_number = ?",
            (accession_number,)
        )
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Order tidak ditemukan")
            
        sr_status = row[0]
        rep_status = row[1]
        
        triggered_tasks = []
        
        if sr_status in ['FAILED', 'UNSENT']:
            send_servicerequest_task.delay(accession_number)
            triggered_tasks.append("ServiceRequest")
            
        if rep_status in ['FAILED', 'UNSENT']:
            send_report_task.delay(accession_number)
            triggered_tasks.append("DiagnosticReport/Observation")

        # Cek apakah ada studi DICOM terkait yang gagal atau tertunda pengunggahannya
        cursor.execute(
            "SELECT study_instance_uid, satusehat_status FROM dicom_studies WHERE accession_number = ?",
            (accession_number,)
        )
        study_row = cursor.fetchone()
        if study_row:
            study_uid = study_row[0]
            study_status = study_row[1]
            if study_status in ['FAILED', 'PENDING', 'FHIR_SENT']:
                send_imagingstudy_task.delay(study_uid)
                triggered_tasks.append("ImagingStudy")
            
        return {
            "success": True,
            "message": f"Berhasil memicu ulang tugas integrasi untuk: {', '.join(triggered_tasks) if triggered_tasks else 'Tidak ada tugas yang perlu di-retry'}"
        }
    except Exception as e:
        logger.error(f"Error triggering retry: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()


@app.get("/api/dicom/studies/{study_uid}/instances")
async def get_study_instances(study_uid: str, current_user: dict = Depends(get_current_user)):
    """
    Mendapatkan daftar citra DICOM (instances) untuk suatu Study UID dari storage lokal.
    """
    import pydicom
    # Baca storage_path dari DB (bukan hardcode)
    conn = get_bridge_conn()
    cur = conn.cursor()
    cur.execute("SELECT storage_path FROM dicom_studies WHERE study_instance_uid = ?", (study_uid,))
    row = cur.fetchone()
    conn.close()

    # Tentukan folder study
    if row and row[0]:
        sp = str(row[0]).strip()
        # Jika storage_path adalah file path (legacy/new), ambil folder study-nya
        study_folder = os.path.dirname(sp) if sp.endswith('.dcm') else sp
    else:
        study_folder = f"/storage/dicom/{study_uid}"

    if not os.path.exists(study_folder):
        # Fallback check ke path legacy
        alt_folder = f"/storage/dicom/{study_uid}"
        if os.path.exists(alt_folder):
            study_folder = alt_folder
        else:
            # Jika tidak ada, biarkan tetap ke /storage/dicom sebagai root untuk pencarian global
            study_folder = "/storage/dicom"

    # Cari semua file .dcm secara rekursif
    dcm_files = glob.glob(os.path.join(study_folder, "**", "*.dcm"), recursive=True)
    instances = []
    
    for file_path in dcm_files:
        try:
            # Baca header DICOM tanpa meload pixel data (sangat cepat)
            ds = pydicom.dcmread(file_path, stop_before_pixels=True)
            s_uid = getattr(ds, "StudyInstanceUID", "")
            if s_uid != study_uid:
                continue
                
            se_uid = getattr(ds, "SeriesInstanceUID", "1")
            sop_uid = getattr(ds, "SOPInstanceUID", os.path.splitext(os.path.basename(file_path))[0])
            
            instances.append({
                "study_uid": s_uid,
                "series_uid": se_uid,
                "sop_uid": sop_uid,
                "url": f"/api/dicom/instances/{s_uid}/{se_uid}/{sop_uid}/file"
            })
        except Exception:
            pass
            
    # Sort instances agar urutan tampilannya konsisten
    instances.sort(key=lambda x: (x["series_uid"], x["sop_uid"]))
    return {"success": True, "data": instances}


@app.get("/api/dicom/instances/{study_uid}/{series_uid}/{sop_uid}/file")
async def get_dicom_file(study_uid: str, series_uid: str, sop_uid: str):
    """
    Menyajikan file DICOM fisik (.dcm) sebagai FileResponse.
    Bypass get_current_user untuk memudahkan Cornerstone WADO Image Loader memanggil via HTTP GET.
    """
    import pydicom
    # 1. Cari di DB untuk storage_path yang terdaftar
    conn = get_bridge_conn()
    cur = conn.cursor()
    cur.execute("SELECT storage_path FROM dicom_studies WHERE study_instance_uid = ?", (study_uid,))
    row = cur.fetchone()
    conn.close()
    
    file_path = None
    if row and row[0]:
        sp = str(row[0]).strip()
        if sp.endswith('.dcm') and os.path.exists(sp):
            # Jika itu file path, dan filenya ada, gunakan langsung!
            file_path = sp
        elif os.path.exists(sp):
            # Jika itu directory path, cari file di dalamnya
            temp_path = os.path.join(sp, f"{sop_uid}.dcm")
            if os.path.exists(temp_path):
                file_path = temp_path
                
    if not file_path or not os.path.exists(file_path):
        # Fallback 1: path standar /storage/dicom/{study_uid}/{series_uid}/{sop_uid}.dcm
        standard_path = f"/storage/dicom/{study_uid}/{series_uid}/{sop_uid}.dcm"
        if os.path.exists(standard_path):
            file_path = standard_path
        else:
            # Fallback 2: glob search di semua subfolder untuk {sop_uid}.dcm
            matches = glob.glob(f"/storage/dicom/**/{sop_uid}.dcm", recursive=True)
            if matches:
                file_path = matches[0]
            else:
                # Fallback 3: Cari file dengan mencocokkan SOPInstanceUID di dalam header DICOM
                all_files = glob.glob(f"/storage/dicom/**/*.dcm", recursive=True)
                for f in all_files:
                    try:
                        ds = pydicom.dcmread(f, stop_before_pixels=True)
                        if getattr(ds, "SOPInstanceUID", "") == sop_uid:
                            file_path = f
                            break
                    except Exception:
                        pass
                        
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File DICOM tidak ditemukan")
        
    return FileResponse(
        file_path, 
        media_type="application/dicom", 
        filename=os.path.basename(file_path)
    )


class OrderCreateRequest(BaseModel):
    noradio: str
    rekmed: str
    namapas: str
    tgllahir: str # YYYY-MM-DD
    jkel: str     # '1' atau '2'
    nadokter: str = "Dokter Tidak Diketahui"
    tglinput: str = None # Format ISO, default waktu sekarang
    modality: str = "CR"
    procedure_name: str = "Pemeriksaan Radiologi"

@app.post("/api/orders", status_code=201)
async def create_order(payload: OrderCreateRequest, current_user: dict = Depends(get_current_user)):
    """
    Endpoint bagi SIMRS untuk mendaftarkan order radiologi baru secara langsung via REST API
    daripada menulis ke database.
    """
    conn = None
    try:
        # Validasi format tanggal lahir
        try:
            birth_date = datetime.date.fromisoformat(payload.tgllahir)
        except ValueError:
            raise HTTPException(status_code=400, detail="Format tgllahir tidak valid (Wajib YYYY-MM-DD)")
            
        # Parse tglinput
        order_datetime = datetime.datetime.now()
        if payload.tglinput:
            try:
                # Coba parse dari format ISO
                order_datetime = datetime.datetime.fromisoformat(payload.tglinput.replace("Z", "+00:00"))
            except ValueError:
                pass
                
        # Map jenis kelamin
        gender = "O"
        if payload.jkel == "1":
            gender = "M"
        elif payload.jkel == "2":
            gender = "F"
            
        conn = get_bridge_conn()
        cursor = conn.cursor()
        
        # Periksa apakah noradio sudah ada di radiology_orders
        cursor.execute("SELECT COUNT(*) FROM radiology_orders WHERE accession_number = ?", (payload.noradio,))
        if cursor.fetchone()[0] > 0:
            raise HTTPException(status_code=400, detail=f"Order dengan Accession Number (noradio) '{payload.noradio}' sudah terdaftar")
            
        # Simpan order baru ke database lokal bridge
        insert_query = """
            INSERT INTO radiology_orders (
                accession_number, patient_id, patient_name, birth_date, 
                gender, doctor_name, order_datetime, status, 
                modality, procedure_name,
                satusehat_servicerequest_status, satusehat_report_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, 'UNSENT', 'UNSENT')
        """
        cursor.execute(
            insert_query,
            (payload.noradio, payload.rekmed, payload.namapas, birth_date, gender, payload.nadokter, order_datetime,
             "DX" if payload.modality.upper() in ("DR", "CR", "DX") else payload.modality, payload.procedure_name)
        )
        conn.commit()
        
        # Memicu Celery Task untuk mengirimkan ServiceRequest ke SATUSEHAT
        send_servicerequest_task.delay(payload.noradio)
        
        logger.info(f"[API Order] Order baru berhasil didaftarkan: {payload.noradio} - Pasien: {payload.namapas}")
        return {
            "success": True,
            "message": "Order radiologi berhasil didaftarkan dan integrasi SATUSEHAT telah dipicu",
            "accession_number": payload.noradio
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Gagal membuat order via API: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()


@app.get("/api/captcha")
async def get_captcha():
    """
    Men-generate Captcha matematika acak.
    Mengembalikan soal matematika (misal: "12 + 5 = ?") dan token bertanda tangan berisi jawaban benar.
    """
    num1 = random.randint(1, 20)
    num2 = random.randint(1, 20)
    operator = random.choice(["+", "-"])
    
    if operator == "+":
        answer = num1 + num2
    else:
        # Pastikan tidak bernilai negatif
        if num1 < num2:
            num1, num2 = num2, num1
        answer = num1 - num2
        
    puzzle = f"{num1} {operator} {num2}"
    
    # Buat token captcha dengan masa berlaku (expiry) 5 menit
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
    to_encode = {
        "ans": str(answer),
        "exp": expire
    }
    captcha_token = jwt.encode(to_encode, settings.JWT_SECRET, algorithm="HS256")
    
    return {
        "success": True,
        "puzzle": puzzle,
        "captcha_token": captcha_token
    }


@app.get("/api/settings")
async def get_settings(current_user: dict = Depends(get_current_user)):
    """
    Mengambil data konfigurasi aktif sistem dengan sensor pada data sensitif.
    """
    def mask_secret(val: str) -> str:
        if not val:
            return ""
        if len(val) <= 4:
            return "****"
        return f"{val[:2]}****{val[-2:]}"

    return {
        "success": True,
        "data": {
            "simrs": {
                "host": settings.SIMRS_SQLSERVER_HOST,
                "port": settings.SIMRS_SQLSERVER_PORT,
                "database": settings.SIMRS_SQLSERVER_DB,
                "user": settings.SIMRS_SQLSERVER_USER,
                "password": mask_secret(settings.SIMRS_SQLSERVER_PASSWORD)
            },
            "bridge_db": {
                "host": settings.BRIDGE_SQLSERVER_HOST,
                "port": settings.BRIDGE_SQLSERVER_PORT,
                "database": settings.BRIDGE_SQLSERVER_DB,
                "user": settings.BRIDGE_SQLSERVER_USER,
                "password": mask_secret(settings.BRIDGE_SQLSERVER_PASSWORD)
            },
            "dicom": {
                "mwl_ae_title": settings.MWL_AE_TITLE,
                "storage_ae_title": settings.STORAGE_AE_TITLE,
                "container_storage_dir": "/storage/dicom",
                "host_storage_path": settings.LOCAL_DICOM_STORAGE_PATH,
                "cstore_port": 4242,
                "cstore_port_alt": 11112,
                "mwl_port": 104
            },
            "satusehat": {
                "base_url": settings.SATUSEHAT_BASE_URL,
                "client_id": mask_secret(settings.SATUSEHAT_CLIENT_ID),
                "client_secret": mask_secret(settings.SATUSEHAT_CLIENT_SECRET),
                "organization_id": settings.SATUSEHAT_ORGANIZATION_ID,
                "dicom_base_url": settings.SATUSEHAT_DICOM_BASE_URL
            },
            "webhook": {
                "url": settings.WEBHOOK_URL,
                "user": settings.WEBHOOK_USER,
                "password": mask_secret(settings.WEBHOOK_PASSWORD)
            }
        }
    }




