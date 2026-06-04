import requests
from app.core.config import settings
from app.core.logging_config import logger


class WebhookNotifier:
    """
    Modul Webhook Callback ke SIMRS.

    Sesuai Panduan Kemenkes SATUSEHAT (Hal. 8-9):
    - Metode: HTTP POST
    - Autentikasi: Basic Authentication
    - Payload JSON wajib: accessionNumber, status, error_code, message
    - Server SIMRS wajib merespons 200 OK dalam < 5 detik

    Jika WEBHOOK_URL dikosongkan di .env, notifikasi di-skip secara silent
    agar backward-compatible dengan konfigurasi lama.
    """

    MAX_RETRIES = 3
    TIMEOUT_SECONDS = 5

    def __init__(self):
        self.webhook_url = settings.WEBHOOK_URL
        self.webhook_user = settings.WEBHOOK_USER
        self.webhook_password = settings.WEBHOOK_PASSWORD

    @property
    def is_configured(self) -> bool:
        """Cek apakah webhook sudah dikonfigurasi."""
        return bool(self.webhook_url and self.webhook_url.strip())

    def send_notification(
        self,
        accession_number: str,
        status: str,
        error_code: str = None,
        message: str = None,
    ):
        """
        Kirim notifikasi webhook ke SIMRS setelah proses upload selesai.

        Args:
            accession_number: Accession Number (kunci pencarian di SIMRS)
            status: "SUCCESS" atau "FAILED"
            error_code: Kode error (hanya jika FAILED), misal "STOWRS_TIMEOUT"
            message: Penjelasan detail status

        Returns:
            bool: True jika berhasil dikirim, False jika gagal atau tidak dikonfigurasi
        """
        if not self.is_configured:
            logger.debug(
                f"Webhook tidak dikonfigurasi, skip notifikasi untuk ACSN {accession_number}"
            )
            return False

        payload = {
            "accessionNumber": accession_number,
            "status": status,
        }

        # Hanya sertakan error_code dan message jika ada
        if error_code:
            payload["error_code"] = error_code
        if message:
            payload["message"] = message

        # Autentikasi Basic Auth sesuai panduan Kemenkes
        auth = None
        if self.webhook_user and self.webhook_password:
            auth = (self.webhook_user, self.webhook_password)

        # Retry mechanism sederhana (3x) sesuai best practice
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                logger.info(
                    f"[Webhook] Mengirim notifikasi ke SIMRS (attempt {attempt}/{self.MAX_RETRIES}): "
                    f"ACSN={accession_number}, Status={status}"
                )

                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    auth=auth,
                    timeout=self.TIMEOUT_SECONDS,
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 200:
                    logger.info(
                        f"[Webhook] Notifikasi berhasil dikirim ke SIMRS: "
                        f"ACSN={accession_number}, Status={status}"
                    )
                    return True
                else:
                    logger.warning(
                        f"[Webhook] SIMRS merespons {response.status_code}: {response.text}"
                    )

            except requests.exceptions.Timeout:
                logger.warning(
                    f"[Webhook] Timeout saat mengirim notifikasi (attempt {attempt}): "
                    f"ACSN={accession_number}"
                )
            except requests.exceptions.ConnectionError:
                logger.warning(
                    f"[Webhook] Koneksi ke SIMRS gagal (attempt {attempt}): "
                    f"URL={self.webhook_url}"
                )
            except Exception as e:
                logger.error(
                    f"[Webhook] Error tidak terduga (attempt {attempt}): {e}"
                )

        logger.error(
            f"[Webhook] Gagal mengirim notifikasi setelah {self.MAX_RETRIES} percobaan: "
            f"ACSN={accession_number}, Status={status}"
        )
        return False


# Ekspor singleton instance
webhook_notifier = WebhookNotifier()
