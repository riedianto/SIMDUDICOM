USE RadiologyBridge;
GO

-- Bersihkan spasi trailing dari rekmed/patient_id (penyebab XMARUM tidak tampilkan worklist)
UPDATE radiology_orders
SET patient_id = RTRIM(patient_id),
    updated_at = GETDATE()
WHERE patient_id <> RTRIM(patient_id);
GO

PRINT 'Trim patient_id selesai.';
GO
