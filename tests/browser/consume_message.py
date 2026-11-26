"""Run the documented public Python interface from an installed client wheel."""

import sys

from login_client import Client


client = Client()
message = client.get_message(message_id=sys.argv[1])
print(message.subject)
