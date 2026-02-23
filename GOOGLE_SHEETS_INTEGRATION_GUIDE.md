# Google Sheets Integration Setup - Step by Step

## Your Sheet Details
- **Sheet ID**: `1BVHu5nW2uGbvLNDP4yATWQSiaBdgL9loY4WVIoC5e0Y`
- **Sheet Name**: Check your Google Sheet

## Step 1: Create Google Apps Script

1. Go to [Google Apps Script](https://script.google.com)
2. Click "New Project" or "+ Project"
3. **Delete the default `myFunction()` code completely**
4. **Copy and paste the entire code** from `GOOGLE_APPS_SCRIPT.js` in this folder
5. Replace the line:
   ```javascript
   var SHEET_ID = "1BVHu5nW2uGbvLNDP4yATWQSiaBdgL9loY4WVIoC5e0Y";
   ```
   with your actual sheet ID (it's already pre-filled above)

## Step 2: Deploy as Web App

1. In Apps Script, click **"Deploy"** (top right)
2. Click **"New Deployment"** 
3. Click the gear icon to select deployment type
4. Select **"Web app"**
5. Fill in the form:
   - **Execute as**: Your Google Account
   - **Who has access**: Anyone
6. Click **"Deploy"**
7. Grant permissions when prompted
8. **Copy the deployment URL** that appears (looks like: `https://script.google.com/macros/s/AKfycbz...`)

## Step 3: Extract Deployment ID

From the URL above, extract the ID between `/s/` and `/exec`

Example: If your URL is `https://script.google.com/macros/s/AKfycbzXxyz123abc/exec`
Then your ID is: `AKfycbzXxyz123abc`

## Step 4: Add to .env File

Open `.env` file and update:
```
SHEET_KEY=AKfycbzXxyz123abc
```

Replace `AKfycbzXxyz123abc` with your actual deployment ID.

## Step 5: Test the Integration

1. Start your Flask app: `python app.py`
2. Send a test WhatsApp message to trigger the bot
3. Complete the form
4. Check your Google Sheet - the lead should appear as a new row

## Data Fields Sent to Sheet

Your app sends these fields:
- **Timestamp** - When the lead was collected
- **WhatsApp ID** - Customer's WA ID
- **Phone Number** - From their response
- **Customer Name** - Extracted from response
- **Q1: Vehicle Type** - What type of vehicle
- **Q2: New or Used** - Purchase preference
- **Q3: Budget** - Price range
- **Q4: Purchase Timeline** - Urgency (determines lead score)
- **Q5: Trade-in** - Trade-in details
- **Q6: Contact Details** - Email/phone
- **Lead Score** - HOT/WARM/COLD based on timeline
- **Preferred Time** - When they want contact

## Troubleshooting

### Issue: "SHEET_KEY not configured"
- Check your `.env` file has the correct deployment ID
- Restart your Flask app after updating .env
- Verify you deployed as "Web app" not "API executable"

### Issue: Leads not appearing in sheet
- Check the Apps Script execution logs:
  1. Go to Apps Script dashboard
  2. Click on your project
  3. View execution logs (clock icon)
- Verify the SHEET_ID in the Apps Script matches your actual sheet
- Check that the sheet has the correct name and is accessible

### Issue: "Error 403"
- Make sure you set "Who has access" to "Anyone" when deploying
- Re-deploy the script if you changed sharing settings

## Optional: Direct API Integration

If you prefer using Google Sheets API directly instead of Apps Script:
1. Enable Google Sheets API in [Google Cloud Console](https://console.cloud.google.com)
2. Create a service account and download JSON credentials
3. Install: `pip install google-api-python-client google-auth-oauthlib`
4. Update your code to use the API directly (see requirements.txt)

---
**Last Updated**: December 15, 2025
**Status**: Ready for Integration
