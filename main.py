from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from pinecone_plugins.assistant.models.chat import Message
from fastapi.responses import Response
from prompts import SYSTEM_MESSAGE
from dotenv import load_dotenv
from twilio.rest import Client
from datetime import datetime
from pinecone import Pinecone
import websockets
import traceback
import requests
import audioop
import asyncio
import base64
import json
import os

load_dotenv(override=True)

# Get environment variables
ULTRAVOX_API_KEY = os.environ.get('ULTRAVOX_API_KEY')
PINECONE_API_KEY = os.environ.get('PINECONE_API_KEY')
N8N_WEBHOOK_URL = os.environ.get('N8N_WEBHOOK_URL')
PUBLIC_URL = os.environ.get('PUBLIC_URL')
PORT = int(os.environ.get('PORT', '8000'))
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')

# Ultravox defaults
ULTRAVOX_MODEL         = "fixie-ai/ultravox-70B"
ULTRAVOX_VOICE         = "Tanya-English"   # or “Mark”
ULTRAVOX_SAMPLE_RATE   = 8000        
ULTRAVOX_BUFFER_SIZE   = 60        

CALENDARS_LIST = {
            "LOCATION1": "CALENDAR_EMAIL1",
            "LOCATION2": "CALENDAR_EMAIL2",
            "LOCATION3": "CALENDAR_EMAIL3",
            # Add more locations / Calendar IDs as needed
        }
                 
app = FastAPI()

# Keep the same session store
sessions = {}

# Just for debugging specific event types
LOG_EVENT_TYPES = [
    'response.content.done',
    'response.done',
    'session.created',
    'conversation.item.input_audio_transcription.completed'
]


@app.get("/")
async def root():
    return {"message": "Twilio + Ultravox Media Stream Server is running!"}

@app.post("/incoming-call")
async def incoming_call(request: Request):
    """
    Handle the inbound call from Twilio. 
    - Fetch firstMessage from N8N
    - Store session data
    - Respond with TwiML containing <Stream> to /media-stream
    """
    form_data = await request.form()
    twilio_params = dict(form_data)
    print('Incoming call')
    # print('Twilio Inbound Details:', json.dumps(twilio_params, indent=2))

    caller_number = twilio_params.get('From', 'Unknown')
    session_id = twilio_params.get('CallSid')
    print('Caller Number:', caller_number)
    print('Session ID (CallSid):', session_id)

    # Fetch first message from N8N
    first_message = "Hey, this is Sara from Agenix AI solutions. How can I assist you today?"
    print("Fetching N8N ...")
    try:
        webhook_response = requests.post(
            N8N_WEBHOOK_URL,
            headers={"Content-Type": "application/json"},
            json={
                "route": "1",
                "number": caller_number,
                "data": "empty"
            },
            # verify=False  # Uncomment if using self-signed certs (not recommended)
        )
        if webhook_response.ok:
            response_text = webhook_response.text
            try:
                response_data = json.loads(response_text)
                if response_data and response_data.get('firstMessage'):
                    first_message = response_data['firstMessage']
                    print('Parsed firstMessage from N8N:', first_message)
            except json.JSONDecodeError:
                # If response is not JSON, treat it as raw text
                first_message = response_text.strip()
        else:
            print(f"Failed to send data to N8N webhook: {webhook_response.status_code}")
    except Exception as e:
        print(f"Error sending data to N8N webhook: {e}")

    # Save session
    session = {
        "transcript": "",
        "callerNumber": caller_number,
        "callDetails": twilio_params,
        "firstMessage": first_message,
        "streamSid": None
    }
    sessions[session_id] = session

    # Respond with TwiML to connect to /media-stream
    host = PUBLIC_URL
    stream_url = f"{host.replace('https', 'wss')}/media-stream"

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Connect>
                <Stream url="{stream_url}">
                    <Parameter name="firstMessage" value="{first_message}" />
                    <Parameter name="callerNumber" value="{caller_number}" />
                    <Parameter name="callSid" value="{session_id}" />
                </Stream>
            </Connect>
        </Response>"""

    return Response(content=twiml_response, media_type="text/xml")


@app.post("/outgoing-call")
async def outgoing_call(request: Request):
    try:
        # Get request data
        data = await request.json() 
        phone_number = data.get('phoneNumber')
        first_message = data.get('firstMessage')
        if not phone_number:
            return {"error": "Phone number is required"}, 400
        
        print('📞 Initiating outbound call to:', phone_number)
        print('📝 With the following first message:', first_message)
        
        # Initialize Twilio client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        # Store call data
        call_data = {
            "originalRequest": data,
            "startTime": datetime.now().isoformat()
        }

         # Respond with TwiML to connect to /media-stream
        host = PUBLIC_URL
        stream_url = f"{host.replace('https', 'wss')}/media-stream"
        
        print('📱 Creating Twilio call with TWIML...')
        call = client.calls.create(
            twiml=f'''<Response>
                        <Connect>
                            <Stream url="{stream_url}">
                                <Parameter name="firstMessage" value="{first_message}" />
                                <Parameter name="callerNumber" value="{phone_number}" />
                            </Stream> 
                        </Connect>
                    </Response>''',
            to=phone_number,
            from_=TWILIO_PHONE_NUMBER,
            status_callback=f"{PUBLIC_URL}/call-status",
            status_callback_event=['initiated', 'ringing', 'answered', 'completed']
        )

        print('📱 Twilio call created:', call.sid)
        # Store call data in sessions
        sessions[call.sid] = {
            "transcript": "",
            "callerNumber": phone_number,
            "callDetails": call_data,
            "firstMessage": first_message,
            "streamSid": None
        }

        return {
            "success": True,
            "callSid": call.sid
        }

    except Exception as error:
        print('❌ Error creating call:', str(error))
        traceback.print_exc()
        return {"error": str(error)}, 500
    

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """
    Handles the Twilio <Stream> WebSocket and connects to Ultravox via WebSocket.
    Includes transcoding audio between Twilio's G.711 µ-law and Ultravox's s16 PCM.
    """
    await websocket.accept()
    print('Client connected to /media-stream (Twilio)')

    # Initialize session variables
    call_sid = None
    session = None
    stream_sid = ''
    uv_ws = None  # Ultravox WebSocket connection
    twilio_task = None  # Store the Twilio handler task

    # Define handler for Ultravox messages
    async def handle_ultravox():
        nonlocal uv_ws, session, stream_sid, call_sid, twilio_task
        try:
            async for raw_message in uv_ws:
                if isinstance(raw_message, bytes):
                    # Agent audio in PCM s16le
                    try:
                        mu_law_bytes = audioop.lin2ulaw(raw_message, 2)
                        payload_base64 = base64.b64encode(mu_law_bytes).decode('ascii')
                    except Exception as e:
                        print(f"Error transcoding PCM to µ-law: {e}")
                        continue  # Skip this audio frame

                    # Send to Twilio as media payload
                    try:
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": payload_base64
                            }
                        }))
                    except Exception as e:
                        print(f"Error sending media to Twilio: {e}")

                else:
                    # Text data message from Ultravox
                    try:
                        msg_data = json.loads(raw_message)
                        # print(f"Received data message from Ultravox: {json.dumps(msg_data)}")
                    except Exception as e:
                        print(f"Ultravox non-JSON data: {raw_message}")
                        continue

                    msg_type = msg_data.get("type") or msg_data.get("eventType")

                    if msg_type == "transcript":
                        role = msg_data.get("role")
                        text = msg_data.get("text") or msg_data.get("delta")
                        final = msg_data.get("final", False)

                        if role and text:
                            role_cap = role.capitalize()
                            session['transcript'] += f"{role_cap}: {text}\n"
                            print(f"{role_cap} says: {text}")

                            if final:
                                print(f"Transcript for {role_cap} finalized.")

                    elif msg_type == "client_tool_invocation":
                        toolName = msg_data.get("toolName", "")
                        invocationId = msg_data.get("invocationId")
                        parameters = msg_data.get("parameters", {})
                        print(f"Invoking tool: {toolName} with invocationId: {invocationId} and parameters: {parameters}")

                        if toolName == "question_and_answer":
                            question = parameters.get('question')
                            print(f'Arguments passed to question_and_answer tool: {parameters}')
                            await handle_question_and_answer(uv_ws, invocationId, question)
                        elif toolName == "schedule_meeting":
                            print(f'Arguments passed to schedule_meeting tool: {parameters}')
                            # Validate required parameters
                            required_params = ["name", "email", "purpose", "datetime", "location"]
                            missing_params = [param for param in required_params if not parameters.get(param)]

                            if missing_params:
                                print(f"Missing parameters for schedule_meeting: {missing_params}")

                                # Inform the agent to prompt the user for missing parameters
                                prompt_message = f"Please provide the following information to schedule your meeting: {', '.join(missing_params)}."
                                tool_result = {
                                    "type": "client_tool_result",
                                    "invocationId": invocationId,
                                    "result": prompt_message,
                                    "response_type": "tool-response"
                                }
                                await uv_ws.send(json.dumps(tool_result))
                            else:
                                await handle_schedule_meeting(uv_ws, session, invocationId, parameters)
                        
                        elif toolName == "hangUp":
                            print("Received hangUp tool invocation")
                            # Send success response back to the agent
                            tool_result = {
                                "type": "client_tool_result",
                                "invocationId": invocationId,
                                "result": "Call ended successfully",
                                "response_type": "tool-response"
                            }
                            await uv_ws.send(json.dumps(tool_result))
                            
                            # End the call process:
                            print(f"Ending call (CallSid={call_sid})")
    
                            # Close Ultravox WebSocket
                            if uv_ws and uv_ws.state == websockets.protocol.State.OPEN:
                                await uv_ws.close()
                            
                            # End Twilio call
                            try:
                                client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                                client.calls(call_sid).update(status='completed')
                                print(f"Successfully ended Twilio call: {call_sid}")
                            except Exception as e:
                                print(f"Error ending Twilio call: {e}")
                            
                            # Send transcript to N8N and cleanup session
                            if session:
                                await send_transcript_to_n8n(session)
                                sessions.pop(call_sid, None)
                            return  # Exit the Ultravox handler

                    elif msg_type == "state":
                        # Handle state messages
                        state = msg_data.get("state")
                        if state:
                            print(f"Agent state: {state}")

                    elif msg_type == "debug":
                        # Handle debug messages
                        debug_message = msg_data.get("message")
                        print(f"Ultravox debug message: {debug_message}")
                        # Attempt to parse nested messages within the debug message
                        try:
                            nested_msg = json.loads(debug_message)
                            nested_type = nested_msg.get("type")

                            if nested_type == "toolResult":
                                tool_name = nested_msg.get("toolName")
                                output = nested_msg.get("output")
                                print(f"Tool '{tool_name}' result: {output}")


                            else:
                                print(f"Unhandled nested message type within debug: {nested_type}")
                        except json.JSONDecodeError as e:
                            print(f"Failed to parse nested message within debug message: {e}. Message: {debug_message}")

                    elif msg_type in LOG_EVENT_TYPES:
                        print(f"Ultravox event: {msg_type} - {msg_data}")
                    else:
                        print(f"Unhandled Ultravox message type: {msg_type} - {msg_data}")

        except Exception as e:
            print(f"Error in handle_ultravox: {e}")
            traceback.print_exc()

    # Define handler for Twilio messages
    async def handle_twilio():
        nonlocal call_sid, session, stream_sid, uv_ws
        try:
            while True:
                message = await websocket.receive_text()
                data = json.loads(message)

                if data.get('event') == 'start':
                    stream_sid = data['start']['streamSid']
                    call_sid = data['start']['callSid']
                    custom_parameters = data['start'].get('customParameters', {})

                    print("Twilio event: start")
                    print("CallSid:", call_sid)
                    print("StreamSid:", stream_sid)
                    print("Custom Params:", custom_parameters)

                    # Extract first_message and caller_number
                    first_message = custom_parameters.get('firstMessage', "Hello, how can I assist you?")
                    caller_number = custom_parameters.get('callerNumber', 'Unknown')

                    if call_sid and call_sid in sessions:
                        session = sessions[call_sid]
                        session['callerNumber'] = caller_number
                        session['streamSid'] = stream_sid
                    else:
                        print(f"Session not found for CallSid: {call_sid}")
                        await websocket.close()
                        return

                    print("Caller Number:", caller_number)
                    print("First Message:", first_message)

                    # Create Ultravox call with first_message
                    uv_join_url = await create_ultravox_call(
                        system_prompt=SYSTEM_MESSAGE,
                        first_message=first_message  # Pass the actual first_message here
                    )

                    if not uv_join_url:
                        print("Ultravox joinUrl is empty. Cannot establish WebSocket connection.")
                        await websocket.close()
                        return

                    # Connect to Ultravox WebSocket
                    try:
                        uv_ws = await websockets.connect(uv_join_url)
                        print("Ultravox WebSocket connected.")
                    except Exception as e:
                        print(f"Error connecting to Ultravox WebSocket: {e}")
                        traceback.print_exc()
                        await websocket.close()
                        return

                    # Start handling Ultravox messages as a separate task
                    uv_task = asyncio.create_task(handle_ultravox())
                    print("Started Ultravox handler task.")

                elif data.get('event') == 'media':
                    # Twilio sends media from user
                    payload_base64 = data['media']['payload']

                    try:
                        # Decode base64 to get raw µ-law bytes
                        mu_law_bytes = base64.b64decode(payload_base64)

                    except Exception as e:
                        print(f"Error decoding base64 payload: {e}")
                        continue  # Skip this payload

                    try:
                        # Transcode µ-law to PCM (s16le)
                        pcm_bytes = audioop.ulaw2lin(mu_law_bytes, 2)
                        
                    except Exception as e:
                        print(f"Error transcoding µ-law to PCM: {e}")
                        continue  # Skip this payload

                    # Send PCM bytes to Ultravox
                    if uv_ws and uv_ws.state == websockets.protocol.State.OPEN:
                        try:
                            await uv_ws.send(pcm_bytes)
                       
                        except Exception as e:
                            print(f"Error sending PCM to Ultravox: {e}")

        except WebSocketDisconnect:
            print(f"Twilio WebSocket disconnected (CallSid={call_sid}).")
            # Attempt to close Ultravox ws
            if uv_ws and uv_ws.state == websockets.protocol.State.OPEN:
                await uv_ws.close()
            # Post the transcript to N8N
            if session:
                await send_transcript_to_n8n(session)
                sessions.pop(call_sid, None)

        except Exception as e:
            print(f"Error in handle_twilio: {e}")
            traceback.print_exc()

    # Start handling Twilio media as a separate task
    twilio_task = asyncio.create_task(handle_twilio())

    try:
        # Wait for the Twilio handler to complete
        await twilio_task
    except asyncio.CancelledError:
        print("Twilio handler task cancelled")
    finally:
        # Ensure everything is cleaned up
        if session and call_sid:
            sessions.pop(call_sid, None)


#
# Handle Twilio call status updates
#
@app.post("/call-status")
async def call_status(request: Request):
    try:
        # Get form data
        data = await request.form()
        print('\n=== 📱 Twilio Status Update ===')
        print('Status:', data.get('CallStatus'))
        print('Duration:', data.get('CallDuration'))
        print('Timestamp:', data.get('Timestamp'))
        print('Call SID:', data.get('CallSid'))
        # print('Full status payload:', dict(data))
        print('\n====== END ======')
        
    except Exception as e:
        print(f"Error getting request data: {e}")
        return {"error": str(e)}, 400

    return {"success": True}

#
# Create an Ultravox serverWebSocket call
#
async def create_ultravox_call(system_prompt: str, first_message: str) -> str:
    """
    Creates a new Ultravox call in serverWebSocket mode and returns the joinUrl.
    """
    url = "https://api.ultravox.ai/api/calls"
    headers = {
        "X-API-Key": ULTRAVOX_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "systemPrompt": system_prompt,
        "model": ULTRAVOX_MODEL,
        "voice": ULTRAVOX_VOICE,
        "temperature":0.1,
        "initialMessages": [
            {
                "role": "MESSAGE_ROLE_USER",  
                "text": first_message
            }
        ],
        "medium": {
            "serverWebSocket": {
                "inputSampleRate": ULTRAVOX_SAMPLE_RATE,   
                "outputSampleRate": ULTRAVOX_SAMPLE_RATE,   
                "clientBufferSizeMs": ULTRAVOX_BUFFER_SIZE
            }
        },
        "selectedTools": [  
            {
                "temporaryTool": {
                    "modelToolName": "question_and_answer",
                    "description": "Get answers to customer questions especially about AI employees",
                    "dynamicParameters": [
                        {
                            "name": "question",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "string",
                                "description": "Question to be answered"
                            },
                            "required": True
                        }
                    ],
                    "timeout": "20s",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "schedule_meeting",
                    "description": "Schedule a meeting for a customer. Returns a message indicating whether the booking was successful or not.",
                    "dynamicParameters": [
                        {
                            "name": "name",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "string",
                                "description": "Customer's name"
                            },
                            "required": True
                        },
                        {
                            "name": "email",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "string",
                                "description": "Customer's email"
                            },
                            "required": True
                        },
                        {
                            "name": "purpose",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "string",
                                "description": "Purpose of the Meeting"
                            },
                            "required": True
                        },
                        {
                            "name": "datetime",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "string",
                                "description": "Meeting Datetime"
                            },
                            "required": True
                        },
                        {
                            "name": "location",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "string",
                                "enum": ["London", "Manchester", "Brighton"],
                                "description": "Meeting location"
                            },
                            "required": True
                        }
                    ],
                    "timeout": "20s",
                    "client": {},
                },
            },
            { "temporaryTool": {
                "modelToolName": "hangUp",
                "description": "End the call",
                "client": {},
                }
            }
        ]
    }

    # print("Creating Ultravox call with payload:", json.dumps(payload, indent=2))  # Enhanced logging

    try:
        resp = requests.post(url, headers=headers, json=payload)
        if not resp.ok:
            print("Ultravox create call error:", resp.status_code, resp.text)
            return ""
        body = resp.json()
        join_url = body.get("joinUrl") or ""
        print("Ultravox joinUrl received:", join_url)  # Enhanced logging
        return join_url
    except Exception as e:
        print("Ultravox create call request failed:", e)
        return ""

#
# Handle "question_and_answer" via Pinecone
#
async def handle_question_and_answer(uv_ws, invocationId: str, question: str):
    try:
        pc = Pinecone(api_key=PINECONE_API_KEY)
        assistant = pc.assistant.Assistant(assistant_name="rag-tool")

        msg = Message(content=question)
        chunks = assistant.chat(messages=[msg], stream=True)

        # Collect entire answer
        answer_message = ""
        for chunk in chunks:
            if chunk and chunk.type == "content_chunk":
                answer_message += chunk.delta.content

        # Respond back to Ultravox
        tool_result = {
            "type": "client_tool_result",
            "invocationId": invocationId,
            "result": answer_message,
            "response_type": "tool-response"
        }
        await uv_ws.send(json.dumps(tool_result))
    except Exception as e:
        print(f"Error in Q&A tool: {e}")
        # Send error result back to Ultravox
        error_result = {
            "type": "client_tool_result",
            "invocationId": invocationId,
            "error_type": "implementation-error",
            "error_message": "An error occurred while processing your request."
        }
        await uv_ws.send(json.dumps(error_result))

#
# Handle "schedule_meeting" calls
#
async def handle_schedule_meeting(uv_ws, session, invocationId: str, parameters):
    """
    Uses N8N to finalize a meeting schedule.
    """
    try:
        name = parameters.get("name")
        email = parameters.get("email")
        purpose = parameters.get("purpose")
        datetime_str = parameters.get("datetime")
        location = parameters.get("location")

        print(f"Received schedule_meeting parameters: name={name}, email={email}, purpose={purpose}, datetime={datetime_str}, location={location}")

        # Validate parameters
        if not all([name, email, purpose, datetime_str, location]):
            raise ValueError("One or more required parameters are missing.")
        
        calendars = CALENDARS_LIST
        calendar_id = calendars.get(location, None)
        if not calendar_id:
            raise ValueError(f"Invalid location: {location}")

        data = {
            "name": name,
            "email": email,
            "purpose": purpose,
            "datetime": datetime_str,
            "calendar_id": calendar_id
        }

        # Fire off the scheduling request to N8N
        payload = {
            "route": "3",
            "number": session.get("callerNumber", "Unknown"),
            "data": json.dumps(data)
        }
        print(f"Sending payload to N8N: {json.dumps(payload, indent=2)}")
        webhook_response = await send_to_webhook(payload)
        parsed_response = json.loads(webhook_response)
        booking_message = parsed_response.get('message', 
            "I'm sorry, I couldn't schedule the meeting at this time.")

        # Return the final outcome to Ultravox
        tool_result = {
            "type": "client_tool_result",
            "invocationId": invocationId,
            "result": booking_message,
            "response_type": "tool-response"
        }
        await uv_ws.send(json.dumps(tool_result))
        print(f"Sent schedule_meeting result to Ultravox: {booking_message}")

    except Exception as e:
        print(f"Error scheduling meeting: {e}")
        # Send error result back to Ultravox
        error_result = {
            "type": "client_tool_result",
            "invocationId": invocationId,
            "error_type": "implementation-error",
            "error_message": "An error occurred while scheduling your meeting."
        }
        await uv_ws.send(json.dumps(error_result))
        print("Sent error message for schedule_meeting to Ultravox.")

#
# Send entire transcript to N8N (end of call)
#
async def send_transcript_to_n8n(session):
    print("Full Transcript:\n", session['transcript'])
    await send_to_webhook({
        "route": "2",
        "number": session.get("callerNumber", "Unknown"),
        "data": session["transcript"]
    })

#
# Send data to N8N webhook
#
async def send_to_webhook(payload):
    if not N8N_WEBHOOK_URL:
        print("Error: N8N_WEBHOOK_URL is not set")
        return json.dumps({"error": "N8N_WEBHOOK_URL not configured"})
        
    try:
        print(f"Sending payload to N8N webhook: {N8N_WEBHOOK_URL}")
        print(f"Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(
            N8N_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code != 200:
            print(f"N8N webhook returned status code {response.status_code}")
            print(f"Response: {response.text}")
            return json.dumps({"error": f"N8N webhook returned status {response.status_code}"})
            
        return response.text
        
    except requests.exceptions.RequestException as e:
        error_msg = f"Error sending data to N8N webhook: {str(e)}"
        print(error_msg)
        return json.dumps({"error": error_msg})

#
# Run app via Uvicorn
#
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
