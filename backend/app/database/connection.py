import pyodbc
from app.core.config import settings
from app.core.logging_config import logger

def get_simrs_conn():
    """
    Mengembalikan koneksi database SIMRS (artha_medika)
    """
    try:
        conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={settings.SIMRS_SQLSERVER_HOST},{settings.SIMRS_SQLSERVER_PORT};"
            f"DATABASE={settings.SIMRS_SQLSERVER_DB};"
            f"UID={settings.SIMRS_SQLSERVER_USER};"
            f"PWD={settings.SIMRS_SQLSERVER_PASSWORD};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=yes;"
            f"Connection Timeout=10;"
        )
        return pyodbc.connect(conn_str)
    except Exception as e:
        logger.error(f"Koneksi ke database SIMRS Gagal ({settings.SIMRS_SQLSERVER_HOST}): {e}")
        raise e

def get_bridge_conn():
    """
    Mengembalikan koneksi database lokal bridge (RadiologyBridge)
    """
    try:
        conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={settings.BRIDGE_SQLSERVER_HOST},{settings.BRIDGE_SQLSERVER_PORT};"
            f"DATABASE={settings.BRIDGE_SQLSERVER_DB};"
            f"UID={settings.BRIDGE_SQLSERVER_USER};"
            f"PWD={settings.BRIDGE_SQLSERVER_PASSWORD};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=yes;"
            f"Connection Timeout=10;"
        )
        return pyodbc.connect(conn_str)
    except Exception as e:
        logger.error(f"Koneksi ke database lokal Bridge Gagal ({settings.BRIDGE_SQLSERVER_HOST}): {e}")
        raise e
