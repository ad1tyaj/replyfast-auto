# ReplyFast Auto - Meta WhatsApp Integration Setup

## Prerequisites

1. **Meta WhatsApp Business Account**: Sign up at [Meta Business](https://business.facebook.com)
2. **WhatsApp Business API Access**: Apply for access through Meta
3. **Redis Server**: For state management
4. **Google Account**: For sheets integration (optional)

## Step 1: Meta WhatsApp Business API Setup

1. Go to [Meta Developers](https://developers.facebook.com/)
2. Create a new app or use existing one
3. Add "WhatsApp Business" product
4. In WhatsApp > Getting Started:
   - Copy your **Access Token** 
   - Copy your **Phone Number ID**
   - Set up webhook URL: `https://yourdomain.com/webhook`
   - Set verify token (any string you choose)

## Step 2: Environment Configuration

Update your `.env` file:

```env
FLASK_ENV=production
PORT=5000

# Redis Configuration
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# Meta WhatsApp Business API
META_API_TOKEN=your_access_token_here
META_PHONE_ID=your_phone_number_id_here

# Google Sheets Integration (optional)
SHEET_KEY=your_google_apps_script_deployment_id_here

# Legacy BSP settings (for future use)
BSP_PROVIDER=meta
```

## Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 4: Start Redis Server

### Windows:
```bash
# Download Redis for Windows or use Docker
docker run -d -p 6379:6379 redis:latest
```

### Linux/Mac:
```bash
redis-server
```

## Step 5: Run the Application

```bash
python app.py
```

## Step 6: Webhook Setup

### For Development (using ngrok):
1. Download and run ngrok: `ngrok http 5000`
2. Copy the HTTPS URL (e.g., `https://abc123.ngrok.io`)
3. In Meta Developer Console, set webhook URL to: `https://abc123.ngrok.io/webhook`
4. Set verify token to match what you put in the webhook verification

### For Production:
1. Deploy to cloud platform (Heroku, AWS, etc.)
2. Use your production domain for webhook URL
3. Ensure HTTPS is enabled

## Step 7: Test the Integration

1. Send a message to your WhatsApp Business number
2. Check the console logs for message processing
3. Verify Redis state management is working
4. Test the complete lead qualification flow

## Troubleshooting

### Common Issues:

1. **403 Forbidden on webhook**: Check verify token matches
2. **Messages not sending**: Verify META_API_TOKEN and META_PHONE_ID
3. **Redis connection failed**: Ensure Redis server is running
4. **Sheets not logging**: Check SHEET_KEY and Google Apps Script setup

### Debug Mode:

Set `FLASK_ENV=development` in `.env` for detailed error messages.

## Features

- ✅ Interactive buttons (up to 3 options)
- ✅ Interactive lists (more than 3 options)
- ✅ State management with Redis
- ✅ Lead scoring (HOT/WARM/COLD)
- ✅ Google Sheets logging
- ✅ Error handling and fallbacks
- ✅ Webhook verification for Meta
- ✅ Health check endpoint

## API Endpoints

- `POST /webhook` - Main WhatsApp webhook
- `GET /webhook` - Webhook verification
- `GET /health` - Health check

## Next Steps

1. Customize the conversation flow in `app.py`
2. Add more question types or branching logic
3. Integrate with your CRM system
4. Add analytics and reporting
5. Scale with load balancers and multiple instances