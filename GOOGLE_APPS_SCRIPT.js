// Google Apps Script for Replyfast Google Sheets Integration
// Deploy as Web App and add the deployment ID to .env as SHEET_KEY

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    
    // IMPORTANT: Replace with your actual Google Sheet ID
    var SHEET_ID = "1BVHu5nW2uGbvLNDP4yATWQSiaBdgL9loY4WVIoC5e0Y";
    var sheet = SpreadsheetApp.openById(SHEET_ID).getActiveSheet();
    
    // Add headers if this is the first row (sheet is empty)
    if (sheet.getLastRow() === 0) {
      sheet.getRange(1, 1, 1, 9).setValues([[
        'Timestamp',
        'WhatsApp ID',
        'Phone Number',
        'Name',
        'Car Type',
        'Model/Budget',
        'Urgency',
        'Test Drive',
        'Contact Details',
        'Lead Score'
      ]]);
    }
    
    // Add the new lead data as a new row
    sheet.appendRow([
      data.timestamp || new Date().toISOString(),
      data.wa_id || '',
      data.phone_number || '',
      data.name || '',
      data.q1_car_type || '',
      data.q2_model_budget || '',
      data.q3_urgency || '',
      data.q4_test_drive || '',
      data.q5_contact_details || '',
      data.lead_score || 0
    ]);
    
    // Log the action
    Logger.log("New lead added: " + data.wa_id + " at " + new Date());
    
    // Return success response
    return ContentService
      .createTextOutput(JSON.stringify({
        success: true,
        message: "Lead data saved successfully",
        timestamp: new Date().toISOString()
      }))
      .setMimeType(ContentService.MimeType.JSON);
      
  } catch (error) {
    Logger.log("Error: " + error.toString());
    return ContentService
      .createTextOutput(JSON.stringify({
        success: false,
        error: error.toString()
      }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// Optional: Function to test the script locally
function testDoPost() {
  var testData = {
    timestamp: new Date().toISOString(),
    wa_id: "1234567890",
    phone_number: "+1234567890",
    name: "Test User",
    q1_car_type: "SUV",
    q2_model_budget: "50000-60000",
    q3_urgency: "Soon",
    q4_test_drive: "Yes",
    q5_contact_details: "test@example.com",
    lead_score: 8.5
  };
  
  var mockEvent = {
    postData: {
      contents: JSON.stringify(testData)
    }
  };
  
  var result = doPost(mockEvent);
  Logger.log(result);
}
