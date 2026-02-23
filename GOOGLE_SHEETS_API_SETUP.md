# Google Sheets API Direct Integration Setup

## Status: ✅ Ready to Use

Your Google Sheets is now configured to use the **Google Sheets API directly** instead of webhooks. This is faster, more reliable, and recommended.

## Prerequisites Installed

Your `requirements.txt` has been updated with:
- `google-auth-oauthlib` - Google authentication
- `google-auth-httplib2` - HTTP transport layer
- `google-api-python-client` - Google Sheets API client

## Configuration Already Done

✅ Service account credentials: `replyfast1-6c01977a46d4.json`
✅ Sheet ID: `1BVHu5nW2uGbvLNDP4yATWQSiaBdgL9loY4WVIoC5e0Y`
✅ API Mode: `direct` (set in `.env`)

## What You Need to Do

### 1. Grant Sheet Access to Service Account

1. Open your Google Sheet: https://docs.google.com/spreadsheets/d/1BVHu5nW2uGbvLNDP4yATWQSiaBdgL9loY4WVIoC5e0Y
2. Click **Share** (top right)
3. Open `replyfast1-6c01977a46d4.json` in your workspace
4. Find the line with `"client_email": "your-email@...iam.gserviceaccount.com"`
5. Copy that email address
6. In Google Sheet share dialog, paste that email
7. Give it **Editor** permissions
8. Click **Share**

### 2. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 3. Test the Integration

Run your app:
```powershell
python app.py
```

You should see in logs:
```
📊 Google Sheets: ✅ Configured (Direct API) - Sheet ID: 1BVHu5nW2uGbvLNDP4yATWQSiaBdgL9loY4WVIoC5e0Y
✅ Google Sheets API Direct Integration
✅ Service Account Authentication
```

### 4. Send a Test Lead

1. Start your WhatsApp bot
2. Complete the form
3. Check your Google Sheet - the lead should appear as a new row with:
   - Timestamp
   - WhatsApp ID
   - Customer name
   - Vehicle details
   - Lead score (HOT/WARM/COLD)
   - Contact info

## Data Fields Automatically Collected

| Field | Source |
|-------|--------|
| Timestamp | Current date/time |
| WhatsApp ID | Customer's WA ID |
| Phone Number | From Q6 response |
| Customer Name | Extracted from Q6 |
| Vehicle Type | Q1 answer |
| New or Used | Q2 answer |
| Budget | Q3 answer |
| Purchase Timeline | Q4 answer (affects lead score) |
| Trade-in | Q5 answer |
| Contact Details | Q6 full response |
| Lead Score | HOT (1 week), WARM (1 month), COLD (other) |
| Preferred Time | Extracted from Q6 |
| Status | Always "New" for new leads |

## Features

✅ **Auto-creates headers** - First sheet gets headers automatically
✅ **Append-only** - New rows added to bottom, existing data never modified
✅ **Service account auth** - No OAuth flow, credential file handles everything
✅ **Error handling** - Falls back to webhook if API fails
✅ **Detailed logging** - See exactly what's being sent to Sheets

## Troubleshooting

### Issue: "Credentials file not found"
- Verify `replyfast1-6c01977a46d4.json` exists in your project root
- Check `.env` has correct filename: `GOOGLE_CREDENTIALS_FILE=replyfast1-6c01977a46d4.json`

### Issue: "403 Permission Denied"
- Service account doesn't have access to the sheet
- **Solution**: Follow step 1 above to share the sheet with the service account email

### Issue: "Sheet ID not found"
- Verify `.env` has correct sheet ID
- Should be: `1BVHu5nW2uGbvLNDP4yATWQSiaBdgL9loY4WVIoC5e0Y`

### Issue: "Google API libraries not available"
- Run: `pip install google-auth-oauthlib google-api-python-client`

## Switching Between Methods

To use Apps Script webhook instead:
1. Set `SHEETS_API_MODE=webhook` in `.env`
2. Provide `SHEET_KEY=your_deployment_id` in `.env`

To switch back to direct API:
1. Set `SHEETS_API_MODE=direct` in `.env`

---
**Last Updated**: December 15, 2025
**Status**: Ready for Production
