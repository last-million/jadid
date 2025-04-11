# Ultravox Twilio Integration

This FastAPI application integrates Twilio with Ultravox for voice interactions, featuring WebSocket streaming and Pinecone for data storage.

## Prerequisites

- Python 3.11
- Environment variables set up in `.env` file
- Twilio account
- Ultravox API key
- Pinecone API key
- N8N webhook URL

## Environment Variables Required

```
PUBLIC_URL=ngrok_url_when_testing_locally / railway app url when deploying
N8N_WEBHOOK_URL=your_webhook_url
PINECONE_API_KEY=your_pinecone_key
ULTRAVOX_API_KEY=your_ultravox_key
PORT=8000  # Optional, defaults to 8000
```

## Installation

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

### Set Up Ngrok (for Local Development)

1. Install ngrok:
```bash
# Using Homebrew (macOS)
brew install ngrok

# Or download from ngrok website
# Visit https://ngrok.com/download and follow installation instructions
```

2. Sign up for a free ngrok account at https://dashboard.ngrok.com/signup

3. Get your authtoken from the ngrok dashboard and configure it:
```bash
ngrok config add-authtoken YOUR_AUTH_TOKEN
```

4. Start ngrok to create a tunnel to your local server:
```bash
ngrok http 8000
```

5. Copy the HTTPS URL provided by ngrok (e.g., `https://xxxx-xx-xx-xxx-xx.ngrok.io`)
   - Use this URL as your `PUBLIC_URL` in the `.env` file
   - Update your Twilio webhook URL with this ngrok URL

Note: The ngrok URL changes each time you restart ngrok unless you have a paid plan. Make sure to update your `.env` file and Twilio webhook URL with the new ngrok URL whenever it changes.

### Set Up Twilio

You'll need a Twilio account and a phone number to receive calls.

#### a. Buy a Twilio Phone Number
- Log in to your Twilio Console.
- Navigate to **Phone Numbers > Buy a Number**.
- Purchase a phone number capable of handling voice calls.

#### b. Configure the Webhook URL
- Go to **Phone Numbers > Manage > Active Numbers**.
- Click on your purchased phone number.
- Scroll down to the **Voice & Fax** section.
- In the **A CALL COMES IN** field, select **Webhook**.
- Enter your webhook URL:
  ```
  https://your-public-url/incoming-call
  ```
  Replace `https://your-public-url` with your actual `PUBLIC_URL`. (Ngrok URL when testing locally, Railway app URL when deploying)
- Set the HTTP method to **POST**.
- Save the configuration.


## Running the Application

Start the FastAPI server:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The application will be available at your ngrok URL: `https://xxxx-xx-xx-xxx-xx.ngrok.io`


### System Message Customization
- **File:** `prompts.py`
- **Variable:** `SYSTEM_MESSAGE`
- **Description:** Defines the assistant's behavior, persona, and conversation guidelines.
- **How to Customize:**
  1. Open `prompts.py`.
  2. Modify the content within `SYSTEM_MESSAGE` to change the assistant's role, persona, and instructions.


### Calendar Emails and Locations

The application can schedule meetings at different locations. You need to update the calendar emails and locations to match your own.

- **Location:** In `main.py`

```python
CALENDARS_LIST = {
            "LOCATION1": "CALENDAR_EMAIL1",
            "LOCATION2": "CALENDAR_EMAIL2",
            "LOCATION3": "CALENDAR_EMAIL3",
            # Add more locations / Calendar IDs as needed
        }
```

- **How to Change:**
  - Replace `LOCATION1`, `LOCATION2`, `LOCATION3` with your actual location names (e.g., "New York", "San Francisco").
  - Replace `CALENDAR_EMAIL1`, `CALENDAR_EMAIL2`, `CALENDAR_EMAIL3` with the email addresses of the calendars where meetings should be scheduled.

  **Example:**
  ```python
  calendars = {
      "New York": "ny-office-calendar@example.com",
      "San Francisco": "sf-office-calendar@example.com",
      "London": "london-office-calendar@example.com",
  }
  ```

## Testing the Application

1. **Make a Call:** Dial the Twilio phone number you configured.
2. **Interact with the Assistant:**
   - The assistant should greet you with a personalized message fetched via N8N.
   - Try asking questions or scheduling a meeting.
3. **Verify Functionality:**
   - Ensure that the assistant responds appropriately.
   - If you scheduled a meeting, check your N8N workflows or calendar to confirm the booking.
4. **Check Logs:**
   - In Replit, view the console logs to see the interactions and debug if necessary.
   - Ensure that transcripts and data are being sent to your N8N webhook.

## Troubleshooting

- **Issue:** Twilio says it cannot reach the webhook URL.
  - **Solution:** Ensure your application is running in Replit and publicly accessible. Double-check the `PUBLIC_URL` and that it's correctly entered in Twilio's settings.


## License

This project is licensed under the MIT License.

**Disclaimer:** This template is provided as-is and is meant for educational purposes. Ensure you comply with all relevant terms of service and legal requirements when using third-party APIs and services.
