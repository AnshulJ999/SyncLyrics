"""
Shared application context to resolve circular dependencies.
"""
from queue import Queue

# Global event queue for communicating between Server and Main Thread
# Commands: "exit", "restart"
queue = Queue()
