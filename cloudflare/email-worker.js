/**
 * Cloudflare Email Worker — receives forwarded emails and POSTs to KinValet API.
 *
 * Setup:
 *   1. Add domain to Cloudflare (mail.kinvalet.com)
 *   2. Enable Email Routing → Catch-all → Send to Worker
 *   3. Deploy this worker
 *   4. Set KINVALET_API_URL and WEBHOOK_SECRET as environment variables
 *
 * Flow:
 *   Gmail filter → mail.kinvalet.com (Cloudflare MX) → this worker → KinValet API
 */

export default {
  async email(message, env, ctx) {
    const apiUrl = env.KINVALET_API_URL || "https://api-production-57040.up.railway.app";
    const webhookSecret = env.WEBHOOK_SECRET || "";

    try {
      // Read the full email body
      const rawEmail = await streamToText(message.raw);

      // Parse headers and body
      const headers = Object.fromEntries(message.headers);
      const from = message.from || headers["from"] || "";
      const to = message.to || headers["to"] || "";
      const subject = headers["subject"] || "";
      const messageId = headers["message-id"] || crypto.randomUUID();

      // Extract plain text body from the raw email
      const body = extractPlainText(rawEmail);

      // POST to KinValet API
      const payload = {
        message_id: messageId,
        from: from,
        to: to,
        subject: subject,
        body_plain: body.substring(0, 5000), // cap at 5KB
        attachments: rawEmail.includes("Content-Disposition: attachment"),
        provider: "cloudflare",
      };

      const response = await fetch(`${apiUrl}/api/v1/email/inbound/cloudflare`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Webhook-Secret": webhookSecret,
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        console.error(`KinValet API returned ${response.status}: ${await response.text()}`);
      }

      // Forward a copy to the archive/backup address
      const forwardTo = env.FORWARD_TO || "kinvalet@kinvalet.com";
      await message.forward(forwardTo);

    } catch (error) {
      console.error("Email processing failed:", error);
      // Still forward on error so no email is lost
      try {
        const forwardTo = env.FORWARD_TO || "kinvalet@kinvalet.com";
        await message.forward(forwardTo);
      } catch (fwdErr) {
        console.error("Forward also failed:", fwdErr);
      }
    }
  },
};

/**
 * Read a ReadableStream into a string.
 */
async function streamToText(stream) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let result = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    result += decoder.decode(value, { stream: true });
  }
  return result;
}

/**
 * Extract plain text body from a raw email (handles multipart MIME).
 */
function extractPlainText(raw) {
  // Look for text/plain part
  const plainMarker = "Content-Type: text/plain";
  const plainIdx = raw.indexOf(plainMarker);

  if (plainIdx !== -1) {
    // Find the blank line after headers (body starts there)
    const bodyStart = raw.indexOf("\n\n", plainIdx);
    if (bodyStart !== -1) {
      let body = raw.substring(bodyStart + 2);
      // Find next MIME boundary or end
      const boundaryIdx = body.indexOf("\n--");
      if (boundaryIdx !== -1) {
        body = body.substring(0, boundaryIdx);
      }
      return body.trim();
    }
  }

  // Fallback: strip HTML tags from the whole thing
  return raw
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .substring(0, 5000)
    .trim();
}
