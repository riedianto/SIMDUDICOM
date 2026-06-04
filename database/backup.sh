#!/bin/bash
# Script untuk backup database lokal RadiologyBridge (SQL Server)

# Set database password (biasanya dilewatkan lewat env)
SA_PASSWORD=${MSSQL_SA_PASSWORD:-"Bridge_Password123!"}
BACKUP_DIR="/var/opt/mssql/backup"
BACKUP_FILE="${BACKUP_DIR}/RadiologyBridge_$(date +%Y%m%d_%H%M%S).bak"

echo "Membuat direktori backup jika belum ada..."
mkdir -p ${BACKUP_DIR}

echo "Memulai proses backup database [RadiologyBridge]..."
/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "${SA_PASSWORD}" -C -Q "BACKUP DATABASE [RadiologyBridge] TO DISK = N'${BACKUP_FILE}' WITH NOFORMAT, INIT, NAME = N'RadiologyBridge-Full Database Backup', SKIP, NOREWIND, NOUNLOAD, STATS = 10"

if [ $? -eq 0 ]; then
    echo "Backup Berhasil Disimpan ke: ${BACKUP_FILE}"
    
    # Hapus backup yang lebih tua dari 7 hari untuk menghemat disk
    echo "Membersihkan file backup lama (lebih dari 7 hari)..."
    find ${BACKUP_DIR} -name "RadiologyBridge_*.bak" -mtime +7 -exec rm {} \;
    echo "Pembersihan selesai."
else
    echo "Backup Gagal!"
    exit 1
fi
