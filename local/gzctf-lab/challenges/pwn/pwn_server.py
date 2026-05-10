import socket
import threading


def handle(conn: socket.socket) -> None:
    try:
        conn.sendall(b"local gzctf pwn\n")
        while True:
            data = conn.recv(4096)
            if not data:
                break
            conn.sendall(data)
    finally:
        conn.close()


def main() -> None:
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 31337))
    server.listen(16)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
