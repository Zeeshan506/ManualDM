import requests
from dotenv import load_dotenv
import os
load_dotenv()

ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN_ZESHAN6A")


def automation_mail(psid, access_token = ACCESS_TOKEN):
    """
    Docstring for automation_mail
    
    :param psid: Description
    :param access_token: Description

    will send a message to the user with the given psid using the Instagram Graph API.
    """

    api_url = f"https://graph.facebook.com/v12.0/{psid}/messages"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    data = {"message": {"text": "Hello from your IG Pro account!"}}

    response = requests.post(api_url, headers=headers, json=data)
    return response.status_code

def fetch_user_info():
    """
    will fetch user info from the database and return it as a list of dictionaries.
    will include the psid of the sender, users info will be taken from env.
    """
    pass