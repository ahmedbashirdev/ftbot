# main.py
import multiprocessing
import db
from da_bot import main as da_main
from supervisor_bot import main as supervisor_main
from client_bot import main as client_main

if __name__ == '__main__':
    db.init_db()
    
    p1 = multiprocessing.Process(target=da_main)
    p2 = multiprocessing.Process(target=supervisor_main)
    p3 = multiprocessing.Process(target=client_main)
    
    p1.start()
    p2.start()
    p3.start()
    
    p1.join()
    p2.join()
    p3.join()
