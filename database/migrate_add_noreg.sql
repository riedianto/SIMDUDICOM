USE RadiologyBridge;
GO

-- Tambah kolom noreg jika belum ada (DB lama sebelum init.sql diperbarui)
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'radiology_orders' AND COLUMN_NAME = 'noreg'
)
BEGIN
    ALTER TABLE radiology_orders ADD noreg NVARCHAR(100) NULL;
    PRINT 'Kolom noreg berhasil ditambahkan.';
END
ELSE
BEGIN
    PRINT 'Kolom noreg sudah ada, skip ALTER TABLE.';
END
GO

-- Normalisasi modality X-ray lama: DR/CR -> DX (untuk worklist mesin DR)
UPDATE radiology_orders
SET modality = 'DX', updated_at = GETDATE()
WHERE modality IN ('DR', 'CR')
  AND status IN ('PENDING', 'SCHEDULED');
GO

PRINT 'Migration migrate_add_noreg.sql selesai.';
GO
