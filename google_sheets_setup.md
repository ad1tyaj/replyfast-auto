# Google Sheets Integration Setup

## Option 1: Google Apps Script Web App (Recommended for Demo)

1. Go to [Google Apps Script](https://script.google.com)
2. Create a new project
3. Replace the default code with:

```javascript
function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    
    // Open your Google Sheet (replace with your sheet ID)
    var sheet = SpreadsheetApp.openById('YOUR_SHEET_ID_HERE').getActiveSheet();
    
    // If this is the first row, add headers
    if (sheet.getLastRow() === 0) {
      sheet.getRange(1, 1, 1, 8).setValues([[
        'Timestamp', 'WhatsApp ID', 'Car Type', 'Model/Budget', 
        'Urgency', 'Test Drive', 'Contact Details', 'Lead Score'
      ]]);
    }
    
    // Add the new lead data
    sheet.appendRow([
      data.timestamp,
      data.wa_id,
      data.q1_car_type,
      data.q2_model_budget,
      data.q3_urgency,
      data.q4_test_drive,
      data.q5_contact_details,
      data.lead_score
    ]);
    
    return ContentService
      .createTextOutput(JSON.stringify({success: true}))
      .setMimeType(ContentService.MimeType.JSON);
      
  } catch (error) {
    return ContentService
      .createTextOutput(JSON.stringify({success: false, error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
```

4. Save the project
5. Deploy as Web App:
   - Click "Deploy" > "New Deployment"
   - Type: Web app
   - Execute as: Me
   - Who has access: Anyone
6. Copy the Web App URL
7. Extract the deployment ID from the URL (the long string between `/s/` and `/exec`)
8. Use this ID as your `SHEET_KEY` in the .env file

## Option 2: Direct Google Sheets API

If you prefer to use the official Google Sheets API:

1. Enable Google Sheets API in Google Cloud Console
2. Create service account credentials
3. Download the JSON key file
4. Install google-api-python-client: `pip install google-api-python-client google-auth`
5. Use the credentials to authenticate API calls

## Environment Variable

Add to your `.env` file:
```
SHEET_KEY=your_google_apps_script_deployment_id_here
```

## Test Your Integration

1. Create a test Google Sheet
2. Set up the Apps Script as described above
3. Update your SHEET_KEY in .env
4. Test a lead submission through your bot
5. Check if data appears in your sheet