import socket, threading, queue
import os
import datetime
import tkinter as tk
from tkinter import scrolledtext, simpledialog


class ChatClient:
    '''
    Main ChatClient class that handles server connection, authentication and message exchange
    in a multithreaded GUI enviroment.
    '''
    def __init__(self):
        # Client socket configuration
        self.DEST_IP = socket.gethostbyname(socket.gethostname())
        self.DEST_PORT = 12345
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        
        # Client variables
        self.username = None
        self.queue_position = None
        self.estimated_wait_time = None
        self.connected_to_chat = False
        self.running = True
        self.in_queue = True
        self.muted = False 

        # Message queue for thread safe communication between reciever and GUI threads
        self.message_queue = queue.Queue()

        # Initialize GUI class
        self.gui = ChatGUI(self)

    def start(self):
        '''
        Initializes client connection, authentication and starts message processing threads.
        '''
        # Connect to server and authenticate
        if not self.connect_to_server():
            return
        
        if not self.authenticate():
            self.client_socket.close()
            return
        
        # Create Threads for concurrent operations
        # One thread for receiving messages for the server
        receive_thread = threading.Thread(target=self.receive_messages)
        # Another thread for processing messages from queue to GUI
        print_thread = threading.Thread(target=self.print_messages)

        # Set threads on daemon so they terminate when main thread closes
        receive_thread.daemon = True
        print_thread.daemon = True

        # Start threads
        receive_thread.start()
        print_thread.start()

        # Start GUI main loop (runs in main thread)
        self.gui.start()

        # Cleanup after GUI closes
        self.running = False
        self.client_socket.close()
        print('Client shutdown complete.')
        
    def connect_to_server(self):
        '''
        Establish a connection to the chat server
        '''
        try:
            self.client_socket.connect((self.DEST_IP, self.DEST_PORT))
            print(f'Connected to LU-Connect server at {self.DEST_IP}: {self.DEST_PORT}')
            return True
        except Exception as e:
            print(f'Failed to connect to server: {e}')
            return False
        
    def authenticate(self):
        '''
        Handles authentication process with the server, including queue management
        Shows required UI for login/register
        '''
        # Loop until client is out of queue
        while self.in_queue:
            try:
                # Wait for message from server, either AUTH_START or QUEUE (from client_handler method)
                server_message = self.client_socket.recv(1024).decode()

                if server_message.startswith('QUEUE:'):
                    # Parse queue status update
                    command, position, wait_time = server_message.split(':')
                    self.queue_position = int(position)
                    self.estimated_wait_time = int(wait_time)
                    print(f'You are #{self.queue_position} in queue. Estimated wait time: {self.estimated_wait_time} minutes')

                elif server_message == 'AUTH_START':
                    # Server is ready to authenticate
                    self.in_queue = False
                    break
            except Exception as e:
                print(f'Error with queue: {e}')
                return False
            
        # Authentication phase begins once client is out of queue
        print('You can now authenticate.')
        self.gui.show() # Displau GUI window
        
        # Loop until succesfully authenticated
        while not self.connected_to_chat:
            # Get authentication choice from user
            command = simpledialog.askstring("Authentication", "Enter 1 for LOGIN or 2 for REGISTER:", parent=self.gui.root).strip()

            if command == '1':
                auth_type = 'LOGIN'
            elif command == '2':
                auth_type = 'REGISTER'
            else:
                self.gui.display_message('Invalid option. Please enter 1 or 2.')
                continue

            # Get credentials from user
            username = simpledialog.askstring("Authentication", "Username:", parent=self.gui.root).strip()
            password = simpledialog.askstring("Authentication", "Password:", show='*', parent=self.gui.root).strip()

            # Send authentication request to server
            auth_message = f'{auth_type} {username} {password}'
            self.client_socket.send(auth_message.encode())

            # Wait for message from server. LOGIN_SUCCESS, LOGIN_FAILED, REGISTER_SUCCESS, or REGISTER_FAILED
            server_message = self.client_socket.recv(1024).decode()

            if server_message.startswith('LOGIN_SUCCESS:'):
                self.username = server_message.split(':')[1]
                self.connected_to_chat = True
                self.gui.display_message(f'Login successful. Welcome {self.username}!')
                self.gui.display_message('You can now start chatting. Type "/exit" to quit.')
                self.gui.display_message('Type "/mute" to mute notifications, "/unmute" to unmute them.')
                return True
            
            elif server_message.startswith('REGISTER_SUCCESS'):
                self.username = server_message.split(':')[1]
                self.connected_to_chat = True
                self.gui.display_message(f'Registration successful. Welcome {self.username}!')
                self.gui.display_message('You can now start chatting. Type "/exit" to quit.')
                self.gui.display_message('Type "/mute" to mute notifications, "/unmute" to unmute them.')
                return True
            
            elif server_message == 'LOGIN_FAILED':
                self.gui.display_message('Invalid username or password.')
            elif server_message == 'REGISTER_FAILED':
                self.gui.display_message('Username already exists.')

            else:
                self.gui.display_message('Authentication failed. Please try again.')

            # Ask if user wants to retry
            command = simpledialog.askstring("Authentication", "Try again? (y/n):", parent=self.gui.root).strip().lower()
            if command != 'y':
                return False

        return False

    def receive_messages(self):
        '''
        Continuosly receives messages from server and places them in the message queue.
        Runs in a seperate thread
        '''
        while self.running:
            try:
                # Blocking call to receive message from server
                server_message = self.client_socket.recv(1024).decode()
                if server_message:
                    # Place message in a thread safe queue for processing
                    self.message_queue.put(server_message)
                else:
                    # Empty msg means server disconnected
                    break
            except Exception as e:
                if self.running:
                    self.gui.display_message(f'Error with receiving message from server: {e}')
                break
    
    def print_messages(self):
        '''
        Retrieves messages from queue and displays them in the GUI.
        Runs in a seperate thread to handle message display without blocking message reception.
        '''
        while self.running:
            try:
                # Get message from queue (with timeout to allow checking running flag)
                server_message = self.message_queue.get(timeout=0.5)
                # Update GUI - this is thread safe because tkinter operations are queued
                self.gui.display_message(server_message)
                # Play notification sound if not muted
                if not self.muted:
                    os.system('afplay noti.mp3')
            except queue.Empty:
                # No message available, continue loop
                continue
            except Exception as e:
                if self.running:
                    self.gui.display_message(f'Error printing message {e}')

    def send_message(self, event=None): # event param for Tkinter Enter key binding
        '''
        Sends message to server and updates local GUI.
        Can be triggerd by button click or enter key press.
        '''
        client_message = self.gui.message_entry.get()

        # Handle command messages
        if client_message.upper() == '/EXIT':
            self.client_socket.send('/EXIT'.encode())
            self.gui.display_message('Exiting chat...')
            self.running = False
            self.gui.root.quit()
            return

        if client_message.upper() == '/MUTE':
            self.muted = True
            self.gui.display_message('Notifications muted.')
            self.gui.message_entry.delete(0, tk.END)
            return

        if client_message.upper() == '/UNMUTE':
            self.muted = False
            self.gui.display_message('Notifications unmuted.')
            self.gui.message_entry.delete(0, tk.END)
            return

        # Send regular chat message
        if client_message:
            self.client_socket.send(client_message.encode())
            timestamp = datetime.datetime.now().strftime('%H:%M')
            # Display own message in chat window
            self.gui.display_message(f'[{timestamp}] You: {client_message}')  
            self.gui.message_entry.delete(0, tk.END)

    def on_closing(self):
        '''
        Handles application shutdown when window is closed.
        Ensures clean termination of socket connections and threads
        '''
        self.running = False
        self.client_socket.close()
        self.gui.root.quit()

class ChatGUI:
    '''
    Handles the GUI for the chat client.
    Manages the display of messages and user input.
    '''
    def __init__(self, client):
        '''
        Initializes the GUI componenets and references the client instance.
        '''
        self.client = client
        self.root = tk.Tk()
        self.root.withdraw()  # Hide the main GUI initially

        # Configure window
        self.root.title('LU-Connect Chat Client')
        self.root.geometry('600x400')

        # Initialize Chat GUI components
        # Scrolledtext widget for displaying chat messages
        self.chat_display = scrolledtext.ScrolledText(self.root, state='disabled')
        #self.chat_display.pack(padx=20, pady=5)
        self.chat_display.pack(padx=20, pady=5, fill=tk.BOTH, expand=True)

        # Entry filed for composing messages
        self.message_entry = tk.Entry(self.root)
        self.message_entry.pack(padx=20, pady=5, fill=tk.X)
        # Bind enter key to send message
        self.message_entry.bind("<Return>", self.client.send_message)

        # Send button
        self.send_button = tk.Button(self.root, text="Send", command=self.client.send_message)
        self.send_button.pack(padx=20, pady=5)
        
        # Set up close window handler
        self.root.protocol("WM_DELETE_WINDOW", self.client.on_closing)

    def start(self):
        '''
        Starts the Tkinter main event loop.
        '''
        self.root.mainloop()

    def show(self):
        '''
        Makes the GUI window visible. Called after connection is established and before authentication
        '''
        self.root.deiconify()

    def display_message(self, message):
        '''
        Thread safely displays a message in the chat window.
        '''
        # enable text widget for editing
        self.chat_display.config(state='normal')
        # add message with newline
        self.chat_display.insert(tk.END, message + '\n')
        # Disable editing again
        self.chat_display.config(state='disabled')
        # Scroll to show most recent message
        self.chat_display.yview(tk.END)

if __name__ == '__main__':
    # Entry point: create and start the client
    client = ChatClient()
    client.start()