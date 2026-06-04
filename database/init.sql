IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = N'RadiologyBridge')
BEGIN
    CREATE DATABASE RadiologyBridge;
END
GO

USE RadiologyBridge;
GO

-- 1. Tabel radiology_orders
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[radiology_orders]') AND type in (N'U'))
BEGIN
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
        
        noreg NVARCHAR(100) NULL,
        status NVARCHAR(50) DEFAULT 'PENDING', -- 'PENDING', 'SCHEDULED', 'COMPLETED', 'CANCELLED'
        
        satusehat_servicerequest_id NVARCHAR(100) NULL,
        satusehat_servicerequest_status NVARCHAR(50) DEFAULT 'UNSENT', -- 'UNSENT', 'SENT', 'FAILED'
        satusehat_imagingstudy_id NVARCHAR(100) NULL,
        satusehat_report_status NVARCHAR(50) DEFAULT 'UNSENT', -- 'UNSENT', 'SENT', 'FAILED'
        
        created_at DATETIME2 DEFAULT GETDATE(),
        updated_at DATETIME2 DEFAULT GETDATE()
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = N'idx_orders_accession' AND object_id = OBJECT_ID(N'[dbo].[radiology_orders]'))
BEGIN
    CREATE INDEX idx_orders_accession ON radiology_orders(accession_number);
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = N'idx_orders_status' AND object_id = OBJECT_ID(N'[dbo].[radiology_orders]'))
BEGIN
    CREATE INDEX idx_orders_status ON radiology_orders(status);
END
GO

-- 2. Tabel dicom_studies
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[dicom_studies]') AND type in (N'U'))
BEGIN
    CREATE TABLE dicom_studies (
        id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
        accession_number NVARCHAR(100) NOT NULL,
        study_instance_uid NVARCHAR(255) NOT NULL UNIQUE,
        series_count INT DEFAULT 0,
        sop_count INT DEFAULT 0,
        storage_path NVARCHAR(500) NULL,
        satusehat_status NVARCHAR(50) DEFAULT 'PENDING', -- 'PENDING', 'FHIR_SENT', 'UPLOADED', 'UPLOAD_PARTIAL', 'FAILED'
        created_at DATETIME2 DEFAULT GETDATE(),
        
        CONSTRAINT fk_study_order FOREIGN KEY (accession_number) 
            REFERENCES radiology_orders(accession_number) ON DELETE CASCADE
    );
END
GO

-- 3. Tabel integration_logs
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[integration_logs]') AND type in (N'U'))
BEGIN
    CREATE TABLE integration_logs (
        id UNIQUEIDENTIFIER PRIMARY KEY DEFAULT NEWID(),
        accession_number NVARCHAR(100) NOT NULL,
        resource_type NVARCHAR(50) NOT NULL, -- 'ServiceRequest', 'ImagingStudy', 'Observation', 'DiagnosticReport'
        action_type NVARCHAR(20) NOT NULL, -- 'POST', 'PUT', 'GET'
        status NVARCHAR(50) NOT NULL, -- 'SUCCESS', 'FAILED'
        request_payload NVARCHAR(MAX) NULL,
        response_payload NVARCHAR(MAX) NULL,
        error_message NVARCHAR(MAX) NULL,
        created_at DATETIME2 DEFAULT GETDATE()
    );
END
GO
