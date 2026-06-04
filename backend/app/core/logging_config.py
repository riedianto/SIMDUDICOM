import logging
import sys

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    # Disable duplicate logging for uvicorn access logs if necessary
    logging.getLogger("uvicorn.access").handlers = []
    
logger = logging.getLogger("radiology_bridge")
