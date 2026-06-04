# 🐧 Panduan Deployment & Operasional Linux Server (SIMDUDICOM)

Dokumen ini berisi panduan cepat untuk melakukan migrasi, instalasi, dan pengelolaan layanan **SIMDUDICOM** di server berbasis Linux (seperti Ubuntu Server atau Debian).

---

## 🛠️ 1. Prasyarat Sistem (Ubuntu/Debian)

Sebelum memulai, hubungkan ke VM Linux Anda menggunakan SSH dan instal Docker beserta Docker Compose:

```bash
# Update package manager
sudo apt-get update

# Instal Docker & Docker Compose Plugin
sudo apt-get install -y docker.io docker-compose-v2

# Pastikan Docker berjalan otomatis saat server menyala
sudo systemctl enable --now docker
```

---

## 📦 2. Pindahkan Proyek ke Server Linux

### Opsi A: Menggunakan Git (Rekomendasi)
Karena repositori lokal Anda sudah dihubungkan ke GitHub, cukup clone di server Linux baru:
```bash
git clone https://github.com/riedianto/SIMDUDICOM.git
cd SIMDUDICOM
```

### Opsi B: Menggunakan SCP (Salin Langsung)
Jika disalin langsung dari Windows Anda (menggunakan Command Prompt/PowerShell Windows):
```bash
scp -r E:\SIMDUDICOM username@ip_server_baru:/home/username/SIMDUDICOM
```

---

## ⚙️ 3. Konfigurasi Lingkungan (`.env`)

Buat berkas `.env` di folder root proyek server Linux Anda:
```bash
nano .env
```
Salin dan lengkapi variabel berikut (sesuaikan dengan kredensial Anda):

```ini
# --- Kredensial Database Jembatan Lokal (SQL Server Docker) ---
BRIDGE_SQLSERVER_HOST=bridge-db
BRIDGE_SQLSERVER_PORT=1433
BRIDGE_SQLSERVER_DB=RadiologyBridge
BRIDGE_SQLSERVER_USER=sa
BRIDGE_SQLSERVER_PASSWORD=Bridge_Password123!

# --- Kredensial Database SIMRS Rumah Sakit (SQL Server Utama) ---
SIMRS_SQLSERVER_HOST=103.167.236.130
SIMRS_SQLSERVER_PORT=1433
SIMRS_SQLSERVER_DB=DB_SIMRS
SIMRS_SQLSERVER_USER=username_db_simrs
SIMRS_SQLSERVER_PASSWORD=password_db_simrs

# --- Konfigurasi Penyimpanan DICOM Fisik ---
# Kosongkan atau biarkan default untuk menyimpan di folder ./storage/dicom di dalam direktori proyek.
# Atau arahkan ke mount path harddisk eksternal (misal: /mnt/storage_besar/dicom)
LOCAL_DICOM_STORAGE_PATH=./storage/dicom

# --- Kredensial Platform SATUSEHAT Kemenkes ---
SATUSEHAT_BASE_URL=https://api-sandbox.kemkes.go.id/fhir-r4/v1
SATUSEHAT_AUTH_URL=https://api-sandbox.kemkes.go.id/oauth2/v1/accesstoken
SATUSEHAT_CLIENT_ID=your_client_id
SATUSEHAT_CLIENT_SECRET=your_client_secret
SATUSEHAT_ORGANIZATION_ID=your_org_id

# --- Konfigurasi Webhook Callback ke SIMRS ---
WEBHOOK_URL=http://api.simrs-anda.com/v1/dicom-callback
WEBHOOK_USER=username_webhook
WEBHOOK_PASSWORD=password_webhook
```

---

## 🚀 4. Perintah Operasional Docker Compose

Jalankan perintah ini di dalam direktori `SIMDUDICOM` pada server Linux:

| Tindakan | Perintah |
|---|---|
| **Menyalakan Layanan** | `sudo docker compose up -d` |
| **Menyalakan & Rebuild Ulang** | `sudo docker compose up -d --build` |
| **Mematikan Layanan** | `sudo docker compose down` |
| **Melihat Status Layanan** | `sudo docker compose ps` |
| **Melihat Log Real-time** | `sudo docker compose logs -f` |
| **Melihat Log Kontainer Spesifik** | `sudo docker compose logs -f [nama-service]` (contoh: `backend-api`) |
| **Mereset & Menghapus Data Cache Redis** | `sudo docker compose exec redis redis-cli flushall` |

---

## 💾 5. Kustomisasi Penyimpanan Berkas DICOM

Berkas DICOM berukuran besar. Jika Anda ingin menyimpannya di partisi disk terpisah (misalnya `/mnt/sdb/dicom`):
1. Pastikan folder tersebut sudah dibuat dan memiliki izin akses:
   ```bash
   sudo mkdir -p /mnt/sdb/dicom
   sudo chmod -R 777 /mnt/sdb/dicom
   ```
2. Edit berkas `.env` dan ganti variabel berikut:
   ```ini
   LOCAL_DICOM_STORAGE_PATH=/mnt/sdb/dicom
   ```
3. Restart kontainer:
   ```bash
   sudo docker compose down && sudo docker compose up -d
   ```

---

## 🛡️ 6. Konfigurasi Firewall Server (UFW)

Untuk mengamankan VM Linux Anda sekaligus membiarkan alat radiologi dan SIMRS terhubung, jalankan perintah berikut:

```bash
# Aktifkan UFW jika belum aktif
sudo ufw enable

# Buka Port Dashboard Web (Nginx)
sudo ufw allow 8026/tcp comment 'SIMDUDICOM Web Dashboard'

# Buka Port DICOM Modality Worklist (C-FIND)
sudo ufw allow 104/tcp comment 'DICOM MWL Port'

# Buka Port DICOM C-STORE Receiver
sudo ufw allow 11112/tcp comment 'DICOM C-STORE Port'
sudo ufw allow 4242/tcp comment 'DICOM C-STORE Direct Port'

# Terapkan perubahan firewall
sudo ufw reload
```

> [!TIP]
> **Rekomendasi Keamanan**: Batasi port DICOM (`104`, `4242`, `11112`) agar hanya bisa diakses oleh IP Alat Radiologi/PACS Anda (misalnya IP `192.168.10.50`):
> `sudo ufw allow from 192.168.10.50 to any port 104 proto tcp`

---

## 🔍 7. Troubleshooting Singkat

### A. Kontainer `db-init` Statusnya Exit dengan Code Selain 0
Kontainer ini adalah *one-shot container* untuk migrasi database. Jika gagal di awal, kemungkinan karena SQL Server (`bridge-db`) belum siap sepenuhnya saat migrasi dijalankan.
* **Solusi**: Jalankan restart khusus untuk kontainer init:
  ```bash
  sudo docker compose restart db-init
  ```

### B. Izin Akses Folder Penyimpanan Terkunci (Permission Denied)
Jika kontainer Docker tidak dapat menulis ke folder penyimpanan DICOM lokal Anda:
* **Solusi**: Berikan izin akses penuh ke folder penyimpanan lokal:
  ```bash
  sudo chmod -R 777 ./storage
  ```
