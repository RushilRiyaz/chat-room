import socket
import threading
import sqlite3
import queue
import datetime
import base64
import hashlib


class ChatServer:
    '''
    Main ChatServer class that handles client connections, authentication and message broadcasting
    in a concurrent, multithreaded enviroment with a client queue system.
    '''
    def __init__(self): 
        # Server socket configuration
        self.HOST_IP = socket.gethostbyname(socket.gethostname())
        self.HOST_PORT = 12345
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.HOST_IP, self.HOST_PORT))
        self.server_socket.listen()

        # Semaphore limits the number of concurrent client connections
        self.connection_semaphore = threading.Semaphore(3)

        # Handling Clients
        self.clients = {}  # Dict to track active clients {username: client_socket}
        self.waiting_queue = queue.Queue() # FIFO queue for clients waiting to connect
        self.lock = threading.Lock() # Lock to prevent race condition

        # Initialize helper classes
        self.encryption = Encryption()
        self.database = DataBase()

        print(f'LU-Connect Server started on {self.HOST_IP}: {self.HOST_PORT}')

    def register_observer(self, username, client_socket):
        with self.lock:
            self.clients[username] = client_socket

    def unregister_observer(self, username):
        with self.lock:
            if username in self.clients:
                del self.clients[username]

    def start(self):
        '''
        Main server loop that accepts incoming connections and creates a new thread for each client.
        This enables parallel handling of multiple client connections.
        '''
        try:
            while True:
                client_socket, client_address = self.server_socket.accept()
                thread = threading.Thread(target=self.client_handler, args=(client_socket, client_address))
                thread.daemon = True # Daemon threads terminate when the main program exits 
                thread.start()
        except KeyboardInterrupt:
            print('LU-Connect Server shutting down...')
            pass
        finally:
            self.server_socket.close()

    def client_handler(self, client_socket, client_address):
        '''
        Handles each client connection in a seperate thread.
        Uses a semaphore to limit concurrent connections and implements a waiting queue for
        clients when defined limit(3) is reached.
        '''
        # Try to acquire the semaphore
        if self.connection_semaphore.acquire(blocking=False):
            # Connection slot available
            print(f'New connection incoming from {client_address}')
            client_socket.send('AUTH_START'.encode())
            self.handle_connected_client(client_socket, client_address)
        else:
            # Server is full, hence put client into waiting queue and notify them of their position and waiting time
            position = self.waiting_queue.qsize() + 1
            wait_time = position * 2 # Estimated wait time in minutes

            try:
                wait_message = f'QUEUE:{position}:{wait_time}'
                client_socket.send(wait_message.encode())
            
                self.waiting_queue.put((client_socket, client_address))
                print(f'Client {client_address} added to waiting queue at position {position}')
            except:
                # Handle connection errors
                client_socket.close()

    def handle_connected_client(self, client_socket, client_address):
        '''
        Handles authentication and message broadcast for a connected client. This method runs
        in its own thread for each client, enabling parallel processing of client interactions.
        '''
        # Authentication phase
        try:
            # Login phase
            username = None
            # loop until succesfull authentication
            while not username: 
                auth_message = client_socket.recv(1024).decode().strip()
                parts = auth_message.split() # Format: {auth_type} {username} {password}

                if len(parts) >= 3:  
                    command = parts[0].upper()
                    username_attempt = parts[1]
                    password = parts[2]

                    # Handle login or registration
                    if command == 'LOGIN':
                        if self.database.authenticate_user(username_attempt, password):
                            username = username_attempt
                            client_socket.send(f'LOGIN_SUCCESS:{username}'.encode())
                        else:
                            client_socket.send(f'LOGIN_FAILED'.encode())
                    elif command == 'REGISTER':
                        if self.database.register_user(username_attempt, password):
                            username = username_attempt
                            client_socket.send(f'REGISTER_SUCCESS:{username}'.encode())
                        else:
                            client_socket.send('REGISTER_FAILED'.encode())
                    else:
                        client_socket.send('INVALID_COMMAND'.encode())
                else:
                    client_socket.send('INVALID FORMAT'.encode())

            # Add authenticated client to active clients dict
            # Use a lock to ensure ensure thread safe modification of shared date
            #!with self.lock:
            #!    self.clients[username] = client_socket
            self.register_observer(username, client_socket)

            # Notify all clients about new user joining chat
            self.notify_observers(f'{username} has joined the chat. Everyone say HI!', 'SERVER')

            # Main loop to receive and broadcast message to all clients
            while True:
                try:
                    message = client_socket.recv(1024).decode().strip()
                    if message:
                        if message.upper() == 'EXIT':
                            break
                        self.notify_observers(message, username)
                    else:
                        # if an empty message is recieved from a client break
                        break
                except:
                    # If error with client break
                    break
        except Exception as e:
            print(f'Error with handle_connected_client: {client_address}\n{e}')
        finally:
            # Cleanup when client disconnects
            #!if username in self.clients:
                #!with self.lock: # Thread safe removal from shared dict
                #!    del self.clients[username]
            self.unregister_observer(username)
            self.notify_observers(f'{username} has left the chat!', 'SERVER')

            client_socket.close()

            # Relase semaphore to allow next client in queue
            self.connection_semaphore.release()
            print(f'Client Connection closed: {client_address}')

            # Process next client in queue 
            self.process_waiting_queue()
            
    def notify_observers(self, message, sender):
        '''
        Broadcast a message to all connected clients except the sender.
        Use thread synchronization to access shared clients dict safely.
        '''
        timestamp = datetime.datetime.now().strftime('%H:%M')
        formatted_message = f'[{timestamp}] {sender}: {message}'

        # save message to database
        self.database.save_message(sender, message)

        # Thread safe access to clients dict
        with self.lock:
            for username, client_socket in self.clients.items(): 
                if username != sender: # Dont send message back to sender
                    try:
                        client_socket.send(formatted_message.encode())
                    except:
                        # Handle failed broadcast by closing and removing client from dict
                        client_socket.close()
                        del self.clients[username]

    def process_waiting_queue(self):
        '''
        Process next client in the waiting queue when a slot becomes available.
        '''
        # Check if there are any waiting clients
        if not self.waiting_queue.empty():
            waiting_client = self.waiting_queue.get() # Get next client in queue

            # Start new thread to handle this client
            thread = threading.Thread(target=self.client_handler, args=waiting_client) # args = (client_socket, client_address)
            thread.daemon = True
            thread.start()

        # Update queue positions for remaining waiting clients
        self.update_waiting_queue_positions()

    def update_waiting_queue_positions(self):
        '''
        Updates all waiting clients with their new queue positions and estimated waiting times.
        Rebuilds the queue to maintain FIFO order while updating clients
        '''
        # convert queue to a list to iterate through it
        queue_list = list(self.waiting_queue.queue)
        self.waiting_queue = queue.Queue() # Create a new empty queue

        # Update each client with their new position and add back to queue
        for position, (client_socket, client_address) in enumerate(queue_list, 1):
            wait_time = position * 2 # Estimated wait time in minutes
            try:
                wait_message = f'QUEUE:{position}:{wait_time}'
                client_socket.send(wait_message.encode())
                self.waiting_queue.put((client_socket, client_address))
            except:
                # If client disconnected just close socket
                client_socket.close()

class DataBase:
    '''
    Handles database operations for user authentication and message storage.
    Uses SQLite for storage of user credentials and chat history.
    '''
    def __init__(self):
        self.db_name = 'lu_connect_database.db'
        self.encryption = Encryption()
        self.setup_database()

    def setup_database(self):
        '''
        Intializes the database schema if it does not exits and creates tables for users and messages.
        '''
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        # Create users table with username as primary key
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL
        )    
        ''')

        # Create messages table
        cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                sender TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL
                )
            ''')

        conn.commit()
        conn.close()

    def register_user(self, username, password):
        '''Register a new user in the database with a hashed password'''
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        try:
            #!cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            # Hash password before storing for security
            hashed_password = self.encryption.hash_password(password)
            cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed_password))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Username already exists (violates primiary key constraint)
            print(f'Error storing register details for {username}. {username} already exists in the database.')
            return False
        finally:
            conn.close()

    def authenticate_user(self, username, password):
        '''
        Authenticate a user by checking username and password against the database.
        Return True if authentication successfull else False.
        '''
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        #!cursor.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password))
        # Hash the provided password and compare with stored hash
        hashed_password = self.encryption.hash_password(password)
        cursor.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, hashed_password))
        
        result = cursor.fetchone()
        conn.close()
        
        return result is not None
    
    def save_message(self, sender, message):
        '''
        Saves a chat message to the database with encryption
        '''
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        #!cursor.execute('INSERT INTO messages (sender, message, timestamp) VALUES (?, ?, ?)', (sender, message, timestamp))
        # Encrypt message before storage
        encrypted_message = self.encryption.encrypt_message(message)
        cursor.execute('INSERT INTO messages (sender, message, timestamp) VALUES (?, ?, ?)', (sender, encrypted_message, timestamp))

        conn.commit()
        conn.close()

class Encryption:
    '''
    Provides encryption and hashing functionality for secure storage and transmission of sensitive data.
    '''
    def __init__(self):
        # Encryption key for XOR Cipher
        # Note: In production this would be stored far more securely
        self.key = 'A-NOT-SO-SECRET-KEY'

    def encrypt_message(self, message):
        '''Encrypts a message using XOR cipher and base64 encoding'''
        encrypted = []
        # Simple XOR based encryption
        for i, char in enumerate(message):
            key_char = self.key[i % len(self.key)]
            encrypted.append(chr(ord(char) ^ ord(key_char)))
            # Convert to base64 for safe storage and transmission
        return base64.b64encode(''.join(encrypted).encode()).decode()
        
    def hash_password(self, password):
        '''Create a secure one way hash of the password using SHA-256'''
        return hashlib.sha256(password.encode()).hexdigest()


if __name__ == "__main__":
    # Entry point: Create and start the server
    server = ChatServer()
    server.start()