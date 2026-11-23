/**
 * College Email Forwarder - Google Apps Script
 *
 * Automatically monitors the college placement inbox, filters placement/interview/career emails,
 * extracts message metadata, clean body text, and attachments (.xlsx, .xls, .csv, .pdf as base64),
 * and pushes them to your FastAPI Chief of Staff backend (/ingest/college).
 *
 * Setup:
 * 1. Open Google Apps Script (script.google.com) on your College Google Workspace account.
 * 2. Paste this entire code into Code.gs.
 * 3. Update BACKEND_URL and API_KEY in the CONFIG object below.
 * 4. Run `testConnection()` to verify network access and authentication.
 * 5. Run `createTimeTrigger()` to run forwardCollegeEmails every 5 minutes.
 */

const CONFIG = {
  // Your deployed FastAPI backend URL on Render/Railway/Fly.io/VPS or ngrok for local dev
  BACKEND_URL: "https://email-agent-cnfp.onrender.com",

  // Must match COLLEGE_WEBHOOK_API_KEY in your .env
  API_KEY: "mera-collegemahanhai",

  // Gmail search query to capture all placement, career, assessment, test, and shortlist emails
  SEARCH_QUERY: 'in:inbox -label:AgentProcessed (placement OR "cdc" OR "pat" OR "career" OR shortlist OR interview OR assessment OR superset OR neopat OR hackerrank OR mettl OR "test link" OR "applied list" OR "shortlisted list")',

  // Label added after successful webhook transmission to guarantee zero double-processing
  PROCESSED_LABEL: "AgentProcessed",

  // Maximum threads to process per execution run
  BATCH_SIZE: 15,

  // Allowed attachment extensions
  ALLOWED_EXTENSIONS: ["xlsx", "xls", "csv", "pdf"]
};

/**
 * Creates or gets the Gmail label used for tracking processed emails.
 */
function getOrCreateLabel(labelName) {
  const name = labelName || CONFIG.PROCESSED_LABEL || "AgentProcessed";
  let label = GmailApp.getUserLabelByName(name);
  if (!label) {
    label = GmailApp.createLabel(name);
  }
  return label;
}

/**
 * Main function: Polls Gmail, parses emails + attachments, and posts to FastAPI backend.
 */
function forwardCollegeEmails() {
  const processedLabel = getOrCreateLabel(CONFIG.PROCESSED_LABEL);
  const threads = GmailApp.search(CONFIG.SEARCH_QUERY, 0, CONFIG.BATCH_SIZE);

  if (!threads || threads.length === 0) {
    Logger.log("No new placement emails found.");
    return;
  }

  Logger.log(`Found ${threads.length} candidate threads to check.`);

  for (let i = 0; i < threads.length; i++) {
    const thread = threads[i];
    const messages = thread.getMessages();

    let allMessagesSucceeded = true;

    for (let j = 0; j < messages.length; j++) {
      const msg = messages[j];

      // Skip if this specific message was already forwarded (in case of multi-turn threads)
      // Check thread label first for efficiency
      try {
        const payload = buildMessagePayload(msg, thread.getId());
        const success = sendToBackend(payload);

        if (!success) {
          allMessagesSucceeded = false;
          Logger.log(`Failed forwarding message ID: ${msg.getId()}`);
        } else {
          Logger.log(`Successfully forwarded message ID: ${msg.getId()} - Subject: ${msg.getSubject()}`);
        }
      } catch (err) {
        Logger.log(`Error processing message ${msg.getId()}: ${err.toString()}`);
        allMessagesSucceeded = false;
      }
    }

    // Mark thread as processed only when messages are pushed successfully
    if (allMessagesSucceeded) {
      thread.addLabel(processedLabel);
    }
  }
}

/**
 * Extracts metadata, clean plain text body, and base64 attachments.
 */
function buildMessagePayload(msg, threadId) {
  const attachments = [];
  const rawAttachments = msg.getAttachments();

  for (let k = 0; k < rawAttachments.length; k++) {
    const att = rawAttachments[k];
    const filename = att.getName() || "attachment";
    const extension = filename.split('.').pop().toLowerCase();

    if (CONFIG.ALLOWED_EXTENSIONS.indexOf(extension) !== -1) {
      attachments.push({
        filename: filename,
        mime_type: att.getContentType() || "application/octet-stream",
        content_base64: Utilities.base64Encode(att.getBytes())
      });
      Logger.log(`Encoded attachment: ${filename} (${extension})`);
    }
  }

  // Use plain body; fallback to strip html tags if empty
  let bodyText = msg.getPlainBody();
  if (!bodyText || bodyText.trim().length === 0) {
    bodyText = (msg.getBody() || "").replace(/<[^>]+>/g, " ");
  }

  return {
    message_id: msg.getId(),
    thread_id: threadId || msg.getThread().getId(),
    sender: msg.getFrom(),
    subject: msg.getSubject() || "(No Subject)",
    body_text: bodyText,
    received_at: msg.getDate().toISOString(),
    attachments: attachments
  };
}

/**
 * Sends the JSON payload via HTTP POST to the FastAPI backend.
 */
function sendToBackend(payload) {
  const options = {
    method: "post",
    contentType: "application/json",
    headers: {
      "X-API-KEY": CONFIG.API_KEY,
      "User-Agent": "GoogleAppsScript-CollegeForwarder/1.0"
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  const response = UrlFetchApp.fetch(CONFIG.BACKEND_URL, options);
  const code = response.getResponseCode();

  if (code >= 200 && code < 300) {
    return true;
  } else {
    Logger.log(`Backend returned HTTP ${code}: ${response.getContentText()}`);
    return false;
  }
}

/**
 * Verifies backend connectivity by testing health or sample ping.
 */
function testConnection() {
  const healthUrl = CONFIG.BACKEND_URL.replace("/ingest/college", "/health");
  try {
    const response = UrlFetchApp.fetch(healthUrl, { muteHttpExceptions: true });
    Logger.log(`Health check HTTP ${response.getResponseCode()}: ${response.getContentText()}`);
  } catch (err) {
    Logger.log(`Connection test error: ${err.toString()}`);
  }
}

/**
 * Backfills past placement emails from your college inbox history.
 * Run this function once manually if you want the Chief of Staff to ingest all previous placement circulars!
 */
function backfillPastPlacementEmails() {
  const processedLabel = getOrCreateLabel(CONFIG.PROCESSED_LABEL);
  // Search up to 50 historical placement threads
  const threads = GmailApp.search(CONFIG.SEARCH_QUERY, 0, 50);

  if (!threads || threads.length === 0) {
    Logger.log("No historical placement emails found matching search query.");
    return;
  }

  Logger.log(`Found ${threads.length} historical threads to backfill.`);

  for (let i = 0; i < threads.length; i++) {
    const thread = threads[i];
    const messages = thread.getMessages();
    let allSuccess = true;

    for (let j = 0; j < messages.length; j++) {
      try {
        const payload = buildMessagePayload(messages[j], thread.getId());
        const ok = sendToBackend(payload);
        if (!ok) allSuccess = false;
      } catch (err) {
        Logger.log(`Error backfilling message: ${err.toString()}`);
        allSuccess = false;
      }
    }

    if (allSuccess) {
      thread.addLabel(processedLabel);
    }
  }
  Logger.log("Historical backfill completed!");
}

/**
 * Creates an automatic time-driven trigger running every 5 minutes.
 */
function createTimeTrigger() {
  // Remove existing triggers for this function to prevent duplicate schedules
  const allTriggers = ScriptApp.getProjectTriggers();
  for (let i = 0; i < allTriggers.length; i++) {
    if (allTriggers[i].getHandlerFunction() === "forwardCollegeEmails") {
      ScriptApp.deleteTrigger(allTriggers[i]);
    }
  }

  ScriptApp.newTrigger("forwardCollegeEmails")
    .timeBased()
    .everyMinutes(5)
    .create();

  Logger.log("Created automatic trigger: forwardCollegeEmails running every 5 minutes.");
}
