import os
import time
import threading
import requests
from app.core.config import settings
from app.core.logging_config import logger


class SatusehatClient:
    def __init__(self):
        self.base_url = settings.SATUSEHAT_BASE_URL
        self.dicom_base_url = settings.SATUSEHAT_DICOM_BASE_URL
        self.auth_url = settings.SATUSEHAT_AUTH_URL
        self.client_id = settings.SATUSEHAT_CLIENT_ID
        self.client_secret = settings.SATUSEHAT_CLIENT_SECRET
        self.organization_id = settings.SATUSEHAT_ORGANIZATION_ID
        self.token = None
        self.token_expiry = 0
        self._token_lock = threading.Lock()

    def get_access_token(self):
        """
        Mendapatkan token akses OAuth2 dari SATUSEHAT.
        Thread-safe: menggunakan Lock agar Celery workers paralel
        tidak memicu race condition saat request token baru.
        Caches token selama masa berlaku (biasanya 50 menit).
        """
        # Fast path: cek token tanpa lock (double-checked locking)
        if self.token and time.time() < (self.token_expiry - 300):
            return self.token

        with self._token_lock:
            # Re-check setelah acquire lock (thread lain mungkin sudah refresh)
            if self.token and time.time() < (self.token_expiry - 300):
                return self.token

            logger.info("Mengambil token akses baru dari SATUSEHAT...")

            try:
                # Mengirim request token (Client Credentials Grant)
                data = {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                }
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                response = requests.post(self.auth_url, data=data, headers=headers, timeout=15)
                response.raise_for_status()

                res_json = response.json()
                self.token = res_json["access_token"]
                # Set expiry time
                expires_in = int(res_json.get("expires_in", 3600))
                self.token_expiry = time.time() + expires_in

                logger.info("Berhasil mengautentikasi dan menyimpan token akses SATUSEHAT.")
                return self.token

            except Exception as e:
                logger.error(f"Gagal mendapatkan token akses SATUSEHAT: {e}")
                raise e

    def _get_headers(self):
        token = self.get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def post_resource(self, resource_type: str, payload: dict):
        """
        Mengirimkan resource FHIR baru (POST) ke SATUSEHAT
        """
        url = f"{self.base_url}/{resource_type}"
        headers = self._get_headers()

        try:
            logger.info(f"Mengirim resource FHIR {resource_type} ke SATUSEHAT...")
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            # Log detail response jika error untuk debugging
            if response.status_code not in [200, 201]:
                logger.error(f"SATUSEHAT {resource_type} POST Gagal. Status: {response.status_code}, Body: {response.text}")

            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.error(f"Error HTTP request POST {resource_type} ke SATUSEHAT: {e}")
            raise e

    def get_resource(self, resource_type: str, resource_id: str):
        """
        Mengambil resource FHIR berdasarkan ID (GET) dari SATUSEHAT
        """
        url = f"{self.base_url}/{resource_type}/{resource_id}"
        headers = self._get_headers()

        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error HTTP request GET {resource_type} dari SATUSEHAT: {e}")
            raise e

    def search_resource(self, resource_type: str, query_params: dict):
        """
        Mencari resource FHIR berdasarkan query parameters (GET) dari SATUSEHAT
        """
        url = f"{self.base_url}/{resource_type}"
        headers = self._get_headers()

        try:
            response = requests.get(url, params=query_params, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error HTTP search {resource_type} dari SATUSEHAT: {e}")
            raise e

    def upload_dicom_stowrs(self, imagingstudy_id: str, dicom_file_path: str):
        """
        Upload file DICOM fisik (.dcm) ke SATUSEHAT via STOW-RS (DICOMweb).
        
        Sesuai Panduan Kemenkes:
        - Endpoint: {dicom_base_url}/dicom/v1/dicomWeb/studies
        - Header wajib: X-ImagingStudy-ID
        - Format: multipart/related; type="application/dicom"
        
        Args:
            imagingstudy_id: ID ImagingStudy yang sudah di-POST ke SATUSEHAT
            dicom_file_path: Path absolut ke file .dcm di storage lokal
            
        Returns:
            dict: Response dari SATUSEHAT
        """
        stowrs_url = f"{self.dicom_base_url}/dicom/v1/dicomWeb/studies"
        token = self.get_access_token()

        if not os.path.exists(dicom_file_path):
            raise FileNotFoundError(f"File DICOM tidak ditemukan: {dicom_file_path}")

        file_size = os.path.getsize(dicom_file_path)
        file_name = os.path.basename(dicom_file_path)
        logger.info(f"Mengupload file DICOM via STOW-RS: {file_name} ({file_size} bytes), ImagingStudy ID: {imagingstudy_id}")

        try:
            # Baca file DICOM sebagai binary
            with open(dicom_file_path, "rb") as dcm_file:
                dicom_data = dcm_file.read()

            # STOW-RS menggunakan multipart/related dengan boundary
            boundary = "MIME_boundary_9876543210"
            
            # Membangun body multipart/related secara manual sesuai standar DICOM PS3.18
            body_parts = []
            body_parts.append(f"--{boundary}\r\n".encode("utf-8"))
            body_parts.append("Content-Type: application/dicom\r\n".encode("utf-8"))
            body_parts.append("Content-Transfer-Encoding: binary\r\n\r\n".encode("utf-8"))
            body_parts.append(dicom_data)
            body_parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
            
            raw_body = b"".join(body_parts)

            headers = {
                "Authorization": f"Bearer {token}",
                "X-ImagingStudy-ID": imagingstudy_id,
                "Accept": "application/dicom+json",
                "Content-Type": f'multipart/related; type="application/dicom"; boundary={boundary}',
                "Content-Length": str(len(raw_body))
            }

            response = requests.post(
                stowrs_url,
                data=raw_body,
                headers=headers,
                timeout=120  # Timeout lebih lama untuk upload file besar
            )

            if response.status_code not in [200, 201]:
                logger.error(
                    f"STOW-RS Upload Gagal. File: {file_name}, "
                    f"Status: {response.status_code}, Body: {response.text}"
                )

            response.raise_for_status()
            logger.info(f"STOW-RS Upload Berhasil: {file_name} -> ImagingStudy {imagingstudy_id}")
            return {"status": "success", "file": file_name, "http_status": response.status_code}

        except requests.exceptions.Timeout:
            logger.error(f"STOW-RS Upload Timeout: {file_name} (>{120}s)")
            raise
        except Exception as e:
            logger.error(f"Error STOW-RS Upload {file_name}: {e}")
            raise e


# Ekspor objek client tunggal (singleton)
satusehat_client = SatusehatClient()
