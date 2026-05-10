import socket
import threading


def handle(conn: socket.socket, addr):
    try:
        conn.sendall(b"local pwn dynamic challenge\n")
        while True:
            data = conn.recv(4096)
            if not data:
                break
            conn.sendall(data)
    finally:
        conn.close()


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 31337))
    srv.listen(16)
    while True:
        conn, addr = srv.accept()
        thread = threading.Thread(target=handle, args=(conn, addr), daemon=True)
        thread.start()


if __name__ == "__main__":
    main()

