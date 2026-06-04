# Radiology Integration Bridge — Project Documentation

Dokumen ini berisi Product Requirement Document (PRD), System Design, dan Panduan Implementasi untuk **Radiology Integration Bridge**. 

Sistem ini dirancang khusus untuk mengintegrasikan SIMRS lama yang menggunakan database **SQL Server** dengan Modality/PACS lokal, serta menjembatani pengiriman data ke **SATUSEHAT Platform** melalui **SATUSEHAT DICOM Router** (menggunakan skenario **MWL di luar DICOM Router**).

---

# Bagian 1: Product Requirement Document (PRD)

## 1. Overview
**Radiology Integration Bridge** adalah middleware integrasi yang menghubungkan:
* **SIMRS Lama (Legacy)**: Sistem informasi rumah sakit berbasis database SQL Server yang tidak dapat dimodifikasi source code-nya.
* **PACS / Modality Radiologi**: Perangkat scanner (CR, DR, CT, MRI, USG) yang membutuhkan Modality Worklist (MWL) untuk menghindari input manual.
* **SATUSEHAT DICOM Router**: Aplikasi lokal Kemenkes yang mengirimkan file DICOM dan mendaftarkan `ImagingStudy` ke SATUSEHAT.

Sistem ini bertugas untuk:
1. Membaca order radiologi dari database SQL Server SIMRS secara real-time (polling).
2. Menyediakan layanan **DICOM Modality Worklist (MWL) SCP** bagi modality lokal.
3. Menjembatani registrasi resource **ServiceRequest** ke SATUSEHAT secara otomatis segera setelah order dibuat di SIMRS.
4. Menerima file DICOM dari modality/PACS (C-STORE), mengarsipkannya secara lokal, dan meneruskannya ke **SATUSEHAT DICOM Router**.
5. Mendeteksi pengisian ekspertise (hasil bacaan) di SIMRS dan otomatis mengirimkan resource **Observation** & **DiagnosticReport** ke SATUSEHAT.
6. Menyediakan Dashboard Web untuk monitoring alur kerja radiologi, status integrasi SATUSEHAT, dan penanganan error (retry).

---

## 2. Goals & Non-Goals

### Primary Goals:
* **Zero Manual Entry**: Menghilangkan proses input manual data pasien di workstation radiologi/modality guna menghindari typo dan mempercepat layanan.
* **Legacy SIMRS Friendly**: Melakukan integrasi tanpa mengubah kode sumber (source code) dari SIMRS lama. Integrasi dilakukan murni di level database SQL Server.
* **SATUSEHAT Compliance**: Mengotomatiskan kepatuhan pengiriman data radiologi ke SATUSEHAT Kemenkes sesuai standar FHIR dan DICOM.
* **Centralized Monitoring**: Menyediakan dashboard berbasis web untuk melacak status pasien dari mulai order dibuat, proses scanning, upload DICOM, hingga pengiriman ekspertise.

### Non-Goals:
* Tidak menggantikan sistem PACS atau RIS (Radiology Information System) utama yang sudah ada.
* Tidak bertindak sebagai DICOM Viewer utama untuk diagnosis dokter.
* Tidak memodifikasi skema tabel bawaan SIMRS lama (hanya membaca tabel order/ekspertise existing).

---

## 3. Alur Integrasi (Workflow SATUSEHAT - MWL di Luar DICOM Router)

Sistem ini mengimplementasikan skema **MWL di Luar DICOM Router** sesuai panduan resmi SATUSEHAT Kemenkes:

```
+------------+             +------------------------------+             +-----------------------+
| SIMRS DB   |  (Order)    |  Radiology Integration Bridge |  (C-FIND)   | Modality / PACS       |
| SQL Server | ----------> |  - SQL Polling Engine        | <---------> |                       |
+------------+             |  - MWL SCP Service           |             +-----------------------+
                           |  - SATUSEHAT Connector       |                         |
                                          |                                         | (C-STORE DICOM)
                                          | (POST ServiceRequest)                   v
                                          v                             +-----------------------+
                                   +--------------+                     | SATUSEHAT             |
                                   |  SATUSEHAT   |                     | DICOM Router          |
                                   |  Platform    | <------------------ |                       |
                                   +--------------+  (POST ImagingStudy) +-----------------------+
```

### Rincian Langkah Kerja (20-Step SATUSEHAT):
1. **Pencatatan Order**: SIMRS menulis data order radiologi baru ke tabel `tbl_hradio` di SQL Server.
2. **Polling & Pendaftaran ServiceRequest**:
   * SQL Polling Engine mendeteksi data baru di `tbl_hradio`.
   * Bridge menyimpan order di database lokal dan menghasilkan **Accession Number** yang unik jika belum dibuat oleh SIMRS.
   * Bridge mengirimkan **POST ServiceRequest** ke SATUSEHAT menggunakan Accession Number sebagai identifier unik.
   * SATUSEHAT merespons dengan **ServiceRequest ID**. Bridge menyimpan ID ini untuk referensi nanti.
3. **Query Worklist oleh Modality**:
   * Modality mengirimkan permintaan C-FIND ke **MWL SCP** milik Bridge.
   * Bridge membalas dengan data pasien dan menyertakan **Accession Number** yang tepat.
4. **Pemeriksaan & Pengiriman DICOM**:
   * Modality melakukan pemindaian pada pasien.
   * Modality mengirimkan berkas DICOM hasil scan ke **DICOM Storage SCP** milik Bridge (untuk arsip lokal) yang kemudian diteruskan ke **SATUSEHAT DICOM Router**, atau Modality mengirimkannya langsung ke SATUSEHAT DICOM Router.
5. **Unggah DICOM & Pendaftaran ImagingStudy**:
   * **SATUSEHAT DICOM Router** mengunggah berkas DICOM ke **SATUSEHAT DICOM Store** dan mendapatkan **Wado URL**.
   * DICOM Router melakukan **POST ImagingStudy** ke SATUSEHAT dengan menyertakan `basedOn` yang merujuk pada `ServiceRequest ID` (dicocokkan via Accession Number).
6. **Ekspertise & Pelaporan Diagnostik**:
   * Dokter spesialis radiologi mengisi ekspertise di SIMRS (ditulis ke database SIMRS SQL Server).
   * SQL Polling Engine mendeteksi adanya ekspertise baru.
   * Bridge mengirimkan **POST Observation** dan **POST DiagnosticReport** ke SATUSEHAT dengan menyertakan referensi `ServiceRequest ID` dan `ImagingStudy ID`.

---

## 4. Requirement Fungsional (FR) & Non-Fungsional (NFR)

### Functional Requirements:
* **FR-001**: Sistem harus dapat membaca order radiologi dari database SQL Server SIMRS secara terjadwal (polling).
* **FR-002**: Sistem harus menyediakan layanan DICOM Modality Worklist (MWL) SCP pada port standar (misal: 104).
* **FR-003**: Sistem harus dapat menerima file DICOM melalui C-STORE (DICOM Storage SCP) dan menyimpannya ke penyimpanan lokal.
* **FR-004**: Sistem harus mengirimkan data resource FHIR (`ServiceRequest`, `Observation`, `DiagnosticReport`) ke API Gateway SATUSEHAT.
* **FR-005**: Sistem harus memiliki antrean (queue) untuk menangani kegagalan jaringan saat mengirim data ke SATUSEHAT dengan fitur auto-retry.
* **FR-006**: Sistem harus menyediakan antarmuka web untuk melacak status tiap pemeriksaan dan log audit.

### Non-Functional Requirements:
* **Database**: Menggunakan **Microsoft SQL Server** sebagai database utama untuk menyimpan konfigurasi, data lokal bridge, antrean, dan log audit.
* **Performance**: Kecepatan respons C-FIND query dari Modality harus kurang dari 2 detik.
* **Security**: Komunikasi dengan REST API Dashboard harus diamankan menggunakan JWT Authentication dan HTTPS.
* **Reliability**: Sistem harus dapat melakukan rekoneksi otomatis ke database SIMRS dan database lokal apabila terjadi putus koneksi.

---

# Bagian 2: System Design (DESIGN.md)

## 1. Arsitektur Komponen Internal

Sistem dikembangkan dengan arsitektur berbasis kontainer (Docker Compose) yang membagi tugas ke dalam beberapa service independen:

```
                                  +------------------------------------+
                                  |    Radiology Integration Bridge    |
                                  |                                    |
+---------------+  Read Order     |  +------------------------------+  |
| SIMRS DB      | --------------> |  | SQL Polling Service (Python) |  |
| (SQL Server)  | <-------------- |  +------------------------------+  |
+---------------+  Read Result    |                 |                  |
                                  |                 v                  |
                                  |  +------------------------------+  |
                                  |  | Local Bridge Database        |  |
                                  |  | (SQL Server - RadiologyDB)   |  |
                                  |  +------------------------------+  |
                                  |       |         |          |       |
                                  |       |         |          v       |
+---------------+  C-FIND (MWL)   |       |         |   +-----------+  |
| Modality /    | <-------------> | ------+         |   | REST API  |  |
| PACS          |                 |                 |   +-----------+  |
+---------------+                 |                 v         |        |
        |                         |  +---------------------+  |        |
        | C-STORE (DICOM)         |  | SATUSEHAT Connector |  |        |
        v                         |  | (Celery / Redis)    |  |        |
+---------------+                 |  +---------------------+  |        |
| DICOM Router  |                 |            |              |        |
| (SATUSEHAT)   |                 +------------|--------------|--------+
+---------------+                              |              |
        |                                      |              |
        v                                      v              v
+----------------------------------------------------------------------+
|                         SATUSEHAT Platform                           |
+----------------------------------------------------------------------+
```

---

## 2. Skema Database Lokal (SQL Server - RadiologyDB)

Database lokal menggunakan **Microsoft SQL Server** untuk menyimpan data kerja internal bridge. Berikut adalah struktur tabel dalam dialek Transact-SQL (T-SQL):

### A. Tabel `radiology_orders`
Menyimpan salinan order dari SIMRS untuk disajikan ke MWL dan dilacak status SATUSEHAT-nya.
```sql
CREATE TABLE radiology_orders (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
    accession_number NVARCHAR(100) NOT NULL UNIQUE,
    patient_id NVARCHAR(100) NOT NULL,
    patient_name NVARCHAR(255) NOT NULL,
    birth_date DATE NOT NULL,
    gender NVARCHAR(10) NOT NULL, -- 'M', 'F', 'O'
    modality NVARCHAR(20) NOT NULL, -- 'CT', 'MR', 'CR', etc.
    procedure_name NVARCHAR(255) NOT NULL,
    doctor_name NVARCHAR(255),
    order_datetime DATETIME2 NOT NULL,
    
    -- Status Alur Kerja Lokal
    status NVARCHAR(50) DEFAULT 'PENDING', -- 'PENDING', 'SCHEDULED', 'COMPLETED', 'CANCELLED'
    
    -- Integrasi SATUSEHAT
    satusehat_servicerequest_id NVARCHAR(100) NULL,
    satusehat_servicerequest_status NVARCHAR(50) DEFAULT 'UNSENT', -- 'UNSENT', 'SENT', 'FAILED'
    satusehat_imagingstudy_id NVARCHAR(100) NULL,
    satusehat_report_status NVARCHAR(50) DEFAULT 'UNSENT', -- 'UNSENT', 'SENT', 'FAILED'
    
    created_at DATETIME2 DEFAULT GETDATE(),
    updated_at DATETIME2 DEFAULT GETDATE()
);

CREATE INDEX idx_orders_accession ON radiology_orders(accession_number);
CREATE INDEX idx_orders_status ON radiology_orders(status);
```

### B. Tabel `dicom_studies`
Menyimpan data citra medis yang berhasil diunggah dan direferensikan oleh DICOM Router.
```sql
CREATE TABLE dicom_studies (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
    accession_number NVARCHAR(100) NOT NULL,
    study_instance_uid NVARCHAR(255) NOT NULL UNIQUE,
    series_count INT DEFAULT 0,
    sop_count INT DEFAULT 0,
    storage_path NVARCHAR(500) NULL,
    satusehat_status NVARCHAR(50) DEFAULT 'PENDING', -- 'PENDING', 'PROCESSED', 'FAILED'
    created_at DATETIME2 DEFAULT GETDATE(),
    
    CONSTRAINT fk_study_order FOREIGN KEY (accession_number) 
        REFERENCES radiology_orders(accession_number) ON DELETE CASCADE
);
```

### C. Tabel `integration_logs`
Menyimpan jejak audit dan respons kesalahan saat berkomunikasi dengan SATUSEHAT API.
```sql
CREATE TABLE integration_logs (
    id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
    accession_number NVARCHAR(100) NOT NULL,
    resource_type NVARCHAR(50) NOT NULL, -- 'ServiceRequest', 'ImagingStudy', 'DiagnosticReport'
    action_type NVARCHAR(20) NOT NULL, -- 'POST', 'PUT', 'GET'
    status NVARCHAR(50) NOT NULL, -- 'SUCCESS', 'FAILED'
    request_payload NVARCHAR(MAX) NULL,
    response_payload NVARCHAR(MAX) NULL,
    error_message NVARCHAR(MAX) NULL,
    created_at DATETIME2 DEFAULT GETDATE()
);
```

---

## 4. Pemetaan Database SIMRS (artha_medika)

Sistem akan membaca data order pemeriksaan dari database SIMRS **artha_medika** yang berjalan pada server **103.167.236.130**.

### A. Tabel Sumber SIMRS:
1. **`tbl_hradio`** (Header Order Radiologi):
   * `noradio`: Kode unik order radiologi (digunakan sebagai **Accession Number**).
   * `rekmed`: Nomor rekam medis pasien (digunakan sebagai **Patient ID**).
   * `namapas`: Nama lengkap pasien (digunakan sebagai **Patient Name**).
   * `tgllahir`: Tanggal lahir pasien (digunakan sebagai **Date of Birth / DOB**).
   * `jkel`: Jenis kelamin pasien (Sex):
     * Nilai `1` mapped to **Male** (`M`)
     * Nilai `2` mapped to **Female** (`F`)
     * Nilai lainnya mapped to **Other/Unknown** (`O`)
   * `drperiksa`: Kode dokter pengirim (referensi ke `tbl_dokter.kodokter`).
2. **`tbl_dokter`** (Master Dokter):
   * `kodokter`: Kode unik dokter (kunci relasi dengan `tbl_hradio.drperiksa`).
   * `nadokter`: Nama lengkap dokter (digunakan sebagai **Referring Physician Name**).
3. **`tbl_dicom`** (Data Gambar Hasil Pemeriksaan - Ditulis oleh Bridge):
   * `noradio`: Menghubungkan ke `tbl_hradio.noradio` (Accession Number).
   * `studyuid`: Study Instance UID dari file DICOM.
   * `seriesuid`: Series Instance UID dari file DICOM.
   * `sopuid`: SOP Instance UID dari file DICOM.
   * `filepath`: Path penyimpanan lokal arsip DICOM di bridge.
   * `wadourl`: URL WADO-RS dari SATUSEHAT DICOM Store (digunakan SIMRS untuk menampilkan gambar secara web-based).
   * `tglinput`: Tanggal data gambar ditulis ke SIMRS.

### B. Query SQL Polling SIMRS:
Berikut rancangan query Transact-SQL (T-SQL) yang digunakan oleh **SQL Polling Engine** untuk menarik data order baru dan menggabungkannya dengan master dokter untuk mendapatkan nama dokter perujuk:

```sql
SELECT 
    hr.noradio AS accession_number,
    hr.rekmed AS patient_id,
    hr.namapas AS patient_name,
    hr.tgllahir AS birth_date,
    CASE 
        WHEN hr.jkel = '1' THEN 'M'
        WHEN hr.jkel = '2' THEN 'F'
        ELSE 'O'
    END AS gender,
    hr.drperiksa AS doctor_code,
    dk.nadokter AS doctor_name,
    hr.tglinput AS order_datetime
FROM 
    tbl_hradio hr WITH (NOLOCK)
LEFT JOIN 
    tbl_dokter dk WITH (NOLOCK) ON hr.drperiksa = dk.kodokter
WHERE 
    hr.tglinput >= DATEADD(day, -1, GETDATE()) -- Optimasi: mengambil data 1 hari terakhir
    -- Dan status belum diproses oleh bridge
```

### C. Query SQL Writeback Gambar ke SIMRS (tbl_dicom):
Setelah berkas DICOM berhasil diterima oleh Storage SCP dan/atau tautan WADO URL didapatkan dari SATUSEHAT, Bridge akan menuliskan data tersebut ke database SIMRS `artha_medika` menggunakan query berikut:

```sql
INSERT INTO tbl_dicom (
    noradio, 
    studyuid, 
    seriesuid, 
    sopuid, 
    filepath, 
    wadourl, 
    tglinput
) VALUES (
    ?, -- noradio (Accession Number)
    ?, -- studyuid (Study Instance UID)
    ?, -- seriesuid (Series Instance UID)
    ?, -- sopuid (SOP Instance UID)
    ?, -- filepath (Path file lokal/URL lokal)
    ?, -- wadourl (WADO URL dari SATUSEHAT)
    GETDATE()
);
```

---

## 3. Technology Stack & Docker Compose

### Backend & DICOM Services:
* **Runtime**: Python 3.12
* **Framework Web**: FastAPI (Uvicorn)
* **DICOM Library**: `pynetdicom` (untuk MWL SCP dan Storage SCP), `pydicom`
* **Task Queue**: Celery & Redis (untuk background processing dan retry logic SATUSEHAT)
* **Database Driver**: `pyodbc` dengan driver **ODBC Driver 18 for SQL Server**

### Frontend Dashboard:
* **Template**: AdminLTE (HTML5, Bootstrap 4, Vanilla JS)
* **Visualisasi**: Chart.js untuk statistik alur kerja.

### Docker Compose Services:
1. `nginx`: Bertindak sebagai Unified Gateway (Reverse Proxy). Tidak hanya untuk lalu lintas HTTP (dashboard web & REST API pada port 80/443), tetapi juga untuk lalu lintas TCP (protokol DICOM) yaitu mem-proxy request MWL C-FIND (port 104) dan C-STORE (port 11112) ke service kontainer yang sesuai.
2. `bridge-db`: SQL Server container untuk database lokal bridge (opsional jika menggunakan server SQL Server rumah sakit).
3. `backend-api`: Layanan FastAPI untuk REST API dan Web Dashboard.
4. `mwl-scp`: Daemon Python untuk melayani pencarian worklist DICOM Modality (port 104).
5. `dicom-receiver`: Daemon Python untuk C-STORE SCP (port 11112) yang bertindak sebagai local archive.
6. `redis`: Broker untuk manajemen antrean pengiriman API.
7. `celery-worker`: Worker pengolah pengunggahan data SATUSEHAT secara asinkron.

---

# Bagian 3: Panduan Implementasi (TASK_INSTRUCTION.md)

## Rencana Fase Pembangunan Proyek

Berikut adalah daftar tugas terperinci untuk membangun sistem **Radiology Integration Bridge**:

### PHASE 1 — PROJECT SETUP
* [ ] **TASK-001**: Strukturkan monorepo proyek.
* [ ] **TASK-002**: Buat konfigurasi awal `docker-compose.yml` yang mencakup Redis, database SQL Server lokal, dan base Python service.
* [ ] **TASK-003**: Inisialisasi skema tabel database lokal pada SQL Server (`radiology_orders`, `dicom_studies`, `integration_logs`).
* [ ] **TASK-004**: Setup boilerplate project FastAPI dengan dukungan `logging` terpusat dan penanganan exception terstruktur.
* [ ] **TASK-005**: Siapkan template AdminLTE berbasis HTML/JS statis yang dimuat langsung oleh FastAPI.

### PHASE 2 — SQL SERVER POLLING ENGINE
* [ ] **TASK-006**: Konfigurasi koneksi database ganda (SIMRS SQL Server & Bridge SQL Server) menggunakan `pyodbc`.
* [ ] **TASK-007**: Buat modul sinkronisasi polling terjadwal (menggunakan interval 5 detik) untuk membaca data order baru dari `tbl_hradio`.
* [ ] **TASK-008**: Implementasikan logika pembuatan **Accession Number** otomatis yang unik bila field tersebut kosong di database SIMRS.
* [ ] **TASK-009**: Implementasikan deteksi pengisian hasil pembacaan ekspertise pada database SIMRS untuk mengubah status order menjadi `COMPLETED`.

### PHASE 3 — DICOM MWL SCP SERVICE
* [ ] **TASK-010**: Implementasikan Server C-FIND (MWL SCP) menggunakan `pynetdicom` yang mendengarkan port `104`.
* [ ] **TASK-011**: Buat pemetaan (mapping) query DICOM Tag hasil pencarian Modality ke query database SQL Server `radiology_orders`.
* [ ] **TASK-012**: Lakukan testing simulasi query MWL menggunakan utilitas baris perintah seperti `findscu` (dcmtk).
* [ ] **TASK-013**: Buat pencatatan audit log untuk setiap request C-FIND yang masuk.

### PHASE 4 — DICOM RECEIVER & LOCAL ARCHIVE
* [ ] **TASK-014**: Implementasikan Server C-STORE (Storage SCP) lokal pada port `11112` untuk menerima data citra dari Modality.
* [ ] **TASK-015**: Buat engine pengarsipan file DICOM lokal dengan struktur folder `/storage/dicom/{StudyInstanceUID}/`.
* [ ] **TASK-016**: Implementasikan parser berkas DICOM menggunakan `pydicom` untuk mengekstrak informasi detail citra (seperti jumlah SOP Instance, Series, dll.) dan menyimpannya ke tabel `dicom_studies`.

### PHASE 5 — SATUSEHAT API INTEGRATION
* [ ] **TASK-017**: Buat modul autentikasi SATUSEHAT untuk mendapatkan token akses OAuth2 dari client ID dan client secret.
* [ ] **TASK-018**: Implementasikan pengirim resource **ServiceRequest** (FHIR JSON) ke SATUSEHAT saat ada order baru terdeteksi.
* [ ] **TASK-019**: Buat background worker (Celery) yang mendengarkan event sukses upload DICOM dari DICOM Router untuk melakukan POST resource **ImagingStudy**.
* [ ] **TASK-020**: Buat modul pengiriman hasil ekspertise (**Observation** & **DiagnosticReport**) ke SATUSEHAT setelah status pemeriksaan lokal dinyatakan `COMPLETED`.
* [ ] **TASK-021**: Implementasikan sistem retry otomatis untuk penanganan kegagalan koneksi API SATUSEHAT dengan strategi exponential backoff.

### PHASE 6 — DASHBOARD & MONITORING UI
* [ ] **TASK-022**: Implementasikan halaman utama Dashboard dengan statistik antrean, kegagalan upload, dan status modality aktif.
* [ ] **TASK-023**: Buat antarmuka monitoring daftar order lengkap dengan fitur pencarian pasien dan status badge integrasi SATUSEHAT.
* [ ] **TASK-024**: Sediakan fitur tombol manual **"Retry"** di UI untuk mengirim ulang resource FHIR yang gagal terkirim.
* [ ] **TASK-025**: Buat halaman Log Viewer untuk mempermudah IT Support menelusuri detail error payload dari SATUSEHAT.

### PHASE 7 — PENGAMANAN & DEPLOYMENT
* [ ] **TASK-026**: Buat otentikasi JWT pada REST API Dashboard untuk mengamankan akses admin.
* [ ] **TASK-027**: Konfigurasi NGINX reverse proxy untuk HTTP (Dashboard/REST API) dan TCP Stream (DICOM MWL Port 104 & Storage SCP Port 11112), serta enkripsi HTTPS (SSL/TLS).
* [ ] **TASK-028**: Buat strategi backup basis data lokal SQL Server terjadwal.

---

# Bagian 4: Konfigurasi Sistem (.env)

Berikut adalah daftar variabel lingkungan (environment variables) yang wajib dikonfigurasi untuk menjalankan sistem:

```env
# 1. Database Connections
SIMRS_SQLSERVER_HOST=103.167.236.130
SIMRS_SQLSERVER_PORT=1433
SIMRS_SQLSERVER_DB=artha_medika
SIMRS_SQLSERVER_USER=sa
SIMRS_SQLSERVER_PASSWORD=secret_password

BRIDGE_SQLSERVER_HOST=bridge-db
BRIDGE_SQLSERVER_PORT=1433
BRIDGE_SQLSERVER_DB=RadiologyBridge
BRIDGE_SQLSERVER_USER=sa
BRIDGE_SQLSERVER_PASSWORD=bridge_password

# 2. Redis Connection
REDIS_HOST=redis
REDIS_PORT=6379

# 3. Security
JWT_SECRET=supersecretjwtkeyforradiologybridge

# 4. SATUSEHAT Platform credentials
SATUSEHAT_BASE_URL=https://api-sandbox.kemkes.go.id/fhir-r4/v1
SATUSEHAT_CLIENT_ID=your_client_id
SATUSEHAT_CLIENT_SECRET=your_client_secret

# 5. DICOM Application Entity (AE) Titles
# AE Title yang digunakan oleh DICOM MWL SCP (Server Worklist)
MWL_AE_TITLE=SIMDUDIM
# AE Title yang digunakan oleh DICOM Storage SCP (Server Penerima Citra)
STORAGE_AE_TITLE=SIMDUDIM_STORE
```

