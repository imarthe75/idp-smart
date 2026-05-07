import uuid
import time
import os

def generate_uuidv7() -> uuid.UUID:
    """
    Genera un UUID v7 compatible con RFC 9562.
    Ideal para bases de datos como PostgreSQL 18.
    """
    timestamp_ms = int(time.time() * 1000)
    ts_bytes = timestamp_ms.to_bytes(6, byteorder='big')
    rand_bytes = os.urandom(10)
    
    # Ensamblar bytes siguiendo el estándar v7
    v7_bytes = bytearray(ts_bytes + rand_bytes)
    # Versión 7: bits 4 a 7 del byte 6
    v7_bytes[6] = (v7_bytes[6] & 0x0f) | 0x70 
    # Variante 2: bits 6 y 7 del byte 8
    v7_bytes[8] = (v7_bytes[8] & 0x3f) | 0x80
    
    return uuid.UUID(bytes=bytes(v7_bytes))
