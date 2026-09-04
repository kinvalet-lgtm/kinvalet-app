/**
 * Cloudflare Email Worker — receives forwarded emails and POSTs to KinValet API.
 *
 * Flow:
 *   Gmail filter → kinvalet.com (Cloudflare MX) → this worker → KinValet API
 *   + forwards a copy to the archive address
 */

export default {
  async email(message, env, ctx) {
    const apiUrl = env.KINVALET_API_URL || "https://api.kinvalet.com";
    const webhookSecret = env.WEBHOOK_SECRET || "";

    try {
      // Read the full raw email
      const rawEmail = await streamToText(message.raw);

      // Parse headers
      const headers = Object.fromEntries(message.headers);
      const from = message.from || headers["from"] || "";
      const to = message.to || headers["to"] || "";
      const subject = headers["subject"] || "";
      const messageId = headers["message-id"] || crypto.randomUUID();

      // Extract the actual email body (not headers)
      const body = extractBody(rawEmail);

      // POST to KinValet API
      const payload = {
        message_id: messageId,
        from: from,
        to: to,
        subject: subject,
        body_plain: body.substring(0, 5000),
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

      // Forward a copy to the archive address
      const forwardTo = env.FORWARD_TO || "kinvalet@kinvalet.com";
      await message.forward(forwardTo);

    } catch (error) {
      console.error("Email processing failed:", error);
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
 * Extract the actual email body from raw MIME email.
 *
 * Handles:
 * - Simple text/plain emails
 * - Multipart MIME (text/plain + text/html)
 * - Base64 encoded parts
 * - Quoted-printable encoded parts
 * - Gmail's specific MIME structure for confirmation emails
 */
function extractBody(raw) {
  // Strategy 1: Find text/plain part in multipart MIME
  const plainBody = extractMimePart(raw, "text/plain");
  if (plainBody && plainBody.length > 20) {
    return plainBody;
  }

  // Strategy 2: Find text/html part and strip tags
  const htmlBody = extractMimePart(raw, "text/html");
  if (htmlBody && htmlBody.length > 20) {
    return stripHtml(htmlBody);
  }

  // Strategy 3: Everything after the first blank line (end of headers)
  // This handles simple non-MIME emails
  const headerEnd = raw.indexOf("\r\n\r\n");
  if (headerEnd !== -1) {
    const afterHeaders = raw.substring(headerEnd + 4);
    // Check if it starts with MIME boundary or actual content
    if (!afterHeaders.startsWith("--") && !afterHeaders.startsWith("Received:")) {
      return afterHeaders.substring(0, 5000).trim();
    }
  }

  // Strategy 4: Just strip all HTML and headers as last resort
  return stripHtml(raw).substring(0, 5000).trim();
}

/**
 * Extract a specific MIME part (text/plain or text/html) from multipart email.
 */
function extractMimePart(raw, contentType) {
  // Find the content-type marker
  const marker = `Content-Type: ${contentType}`;
  let idx = raw.indexOf(marker);

  // Try case variations
  if (idx === -1) idx = raw.indexOf(marker.toLowerCase());
  if (idx === -1) idx = raw.indexOf(`content-type: ${contentType}`);
  if (idx === -1) return null;

  // Find the blank line after this part's headers (body starts there)
  const bodyStart = raw.indexOf("\r\n\r\n", idx);
  const bodyStartAlt = raw.indexOf("\n\n", idx);
  const start = bodyStart !== -1 ? bodyStart + 4 :
                bodyStartAlt !== -1 ? bodyStartAlt + 2 : -1;
  if (start === -1) return null;

  let body = raw.substring(start);

  // Find the end (next MIME boundary)
  const boundaryIdx = body.indexOf("\n--");
  if (boundaryIdx !== -1) {
    body = body.substring(0, boundaryIdx);
  }

  // Check for transfer encoding
  const headerBlock = raw.substring(idx, start);

  // Handle base64 encoding
  if (headerBlock.toLowerCase().includes("base64")) {
    try {
      body = atob(body.replace(/\s/g, ""));
    } catch (e) {
      // Not valid base64, use as-is
    }
  }

  // Handle quoted-printable encoding
  if (headerBlock.toLowerCase().includes("quoted-printable")) {
    body = decodeQuotedPrintable(body);
  }

  return body.trim();
}

/**
 * Decode quoted-printable encoding.
 */
function decodeQuotedPrintable(str) {
  return str
    .replace(/=\r?\n/g, "")  // soft line breaks
    .replace(/=([0-9A-Fa-f]{2})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
}

/**
 * Strip HTML tags and decode entities.
 */
function stripHtml(html) {
  return html
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>/gi, "\n\n")
    .replace(/<\/div>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
