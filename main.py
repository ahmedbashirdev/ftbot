# main.py
import logging
import multiprocessing
import db
from da_bot import main as da_main
from supervisor_bot import main as supervisor_main
from client_bot import main as client_main

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                   level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == '__main__':
    try:
        logger.info("Initializing database...")
        db.init_db()
        logger.info("Starting all bots...")
        
        p1 = multiprocessing.Process(target=da_main)
        p2 = multiprocessing.Process(target=supervisor_main)
        p3 = multiprocessing.Process(target=client_main)
        
        p1.start()
        p2.start()
        p3.start()
        
        p1.join()
        p2.join()
        p3.join()
        
    except Exception as e:
        logger.error(f"Error in main: {e}")
