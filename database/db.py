import mysql.connector
from mysql.connector import Error
from config import Config


def get_connection():
    return mysql.connector.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DATABASE,
        autocommit=False,
        use_pure=True,
        connection_timeout=5,
    )


def query(sql, params=(), fetch=False, dictionary=True):
    conn = get_connection()
    cur = conn.cursor(dictionary=dictionary)
    try:
        cur.execute(sql, params)
        if fetch:
            result = cur.fetchall()
        elif sql.lstrip().upper().startswith(('INSERT', 'REPLACE')):
            result = cur.lastrowid
        else:
            result = cur.rowcount
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def execute_many(sql, rows):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.executemany(sql, rows)
        conn.commit()
        return cur.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def health_check():
    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute('SELECT 1')
        cur.fetchone()
        return True, None
    except Exception as exc:
        return False, str(exc)
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()


def transaction():
    """Return a MySQL connection/cursor pair for an explicit transaction."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    return conn, cur
