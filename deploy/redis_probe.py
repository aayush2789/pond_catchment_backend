import socket

s = socket.create_connection(("172.17.0.110", 6379), 5)
s.sendall(b"PING\r\n")
print("redis reply:", s.recv(64))
s.sendall(b"SELECT 1\r\n")
print("select 1:", s.recv(64))
s.sendall(b"SET pond:probe ok\r\n")
print("set:", s.recv(64))
s.sendall(b"GET pond:probe\r\n")
print("get:", s.recv(64))
s.close()
