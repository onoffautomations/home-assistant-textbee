# TextBee for Home Assistant  
### Custom Integration by **OnOff Automations**

Custom Home Assistant integration for [TextBee](https://textbee.dev/) – built by **OnOff Automations** – to send and receive SMS/MMS via your TextBee gateway devices.

The **TextBee** Home Assistant integration allows full SMS automation through one or more TextBee gateway devices.  
You can send SMS messages, receive inbound messages instantly via webhook, automate based on keywords, track message stats, and monitor device diagnostics.

> ⚠️ **TextBee Limitations (as of now):**
> - Picture messaging (MMS) is **not supported** yet  
> - Signal strength & battery reporting are **not fully supported** by TextBee yet  

---

## 🚀 Features

### 📱 Multi-Device Support  
Each TextBee gateway under your account becomes its own device in Home Assistant.

### 📊 Per-Device Sensors  
Each device exposes the following sensors:

- **Status** (online/offline/etc.) – _Diagnostic_  
- **Signal Bars** – _Diagnostic_  
- **Battery Level** – _Diagnostic_  
- **Device ID** (with raw attributes) – _Diagnostic_  
- **Registered** (true/false) – _Diagnostic_
- **Last Message Text** (inbound/outbound)
- **Last Incoming Number**
- **Last Outgoing Number**
- **Last Incoming Text**
- **Last Outgoing Text**

### 📤 SMS Sending Service  
A single unified service:

```text
textbee.send_sms
```

Supports:
- Single SMS  
- Bulk SMS (comma-separated numbers)  
- Message content (required)  
- Optional `media_urls` (for future MMS support)

---

# 📦 Installation

You can install TextBee using **HACS (Recommended)** or manually.

---

## 🔵 Option 1 — Install via HACS (Custom Repository)

1. Open **HACS → Integrations**
2. Click **⋮ menu → Custom repositories**
3. Add:

```text
https://github.com/onoffautomations/home-assistant-textbee
```

Repository type: **Integration**

4. Install via HACS  
5. Restart Home Assistant  
6. Add integration: **Settings → Devices & Services → Add Integration → TextBee**

---

## 🔵 Option 2 — Manual Installation

1. Download or clone this repository  
2. Copy `custom_components/textbee` into:

```text
<config>/custom_components/textbee
```

3. Restart Home Assistant  
4. Add integration as usual.

---

# 🔔 Ganerate API

Ganerate a API in Textbee:

- Log in to your TextBee account at https://app.textbee.dev/  
- Generate an API key and use it in the Home Assistant integration config.

---

# ⚙️ Service: `textbee.send_sms`

### Example:

```yaml
service: textbee.send_sms
data:
  device_id: "device_1"
  recipients: "+18451234567, +18885557777"
  message: "Reminder: Shacharis is at 7:15am"
```

---

# 📡 Services

## `textbee.send_sms`

Send a single SMS, bulk SMS, or a picture message.

### Example service call:

```yaml
service: textbee.send_sms
data:
  device_id: "device_1"
  recipients: "+15551234567, +15557654321"
  message: "Hello from Home Assistant 👋"
  media_urls: "https://example.com/image1.jpg, https://example.com/image2.jpg"
```

### Fields:

- `device_id` (string, required): ID of the TextBee gateway device.  
- `recipients` (string or list, required):
  - `"+15551234567"` or  
  - `["+15551234567", "+15557654321"]` or  
  - comma-separated string.  
- `message` (string, required): SMS text.  
- `media_urls` (string or list, optional):  
  One or more URLs to images/media; if provided, a picture/MMS is sent when supported by TextBee.

---

# 🤖 Example automations

## 1. Notify when a specific number texts you

Trigger a mobile push when a specific number sends any SMS.

```yaml
alias: TextBee - Alert when VIP texts
mode: single
trigger:
  - platform: state
    entity_id: sensor.textbee_device_1_last_incoming_text
condition:
  - condition: template
    value_template: >
      {{ states('sensor.textbee_device_1_last_incoming_number') == '+15551234567' }}
action:
  - service: notify.mobile_app_my_phone
    data:
      title: "New SMS from VIP"
      message: >
        {{ states('sensor.textbee_device_1_last_incoming_text') }}
```

---

## 2. Keyword automation – turn on a scene via SMS

Turn on a scene if someone sends you an SMS that contains a keyword like `LIGHTS ON`.

```yaml
alias: TextBee - Control lights via SMS
mode: single
trigger:
  - platform: state
    entity_id: sensor.textbee_device_1_last_incoming_text
condition:
  - condition: template
    value_template: >
      {% set text = states('sensor.textbee_device_1_last_incoming_text') | lower %}
      {{ 'lights on' in text }}
action:
  - service: scene.turn_on
    target:
      entity_id: scene.shul_full_on
  - service: textbee.send_sms
    data:
      device_id: "device_1"
      recipients: >
        {{ states('sensor.textbee_device_1_last_incoming_number') }}
      message: "Lights are now on ✅"
```

---

## 3. Forward all incoming SMS into a HA log / dashboard

Send every incoming SMS to a logbook message & persistent notification:

```yaml
alias: TextBee - Log all incoming SMS
mode: parallel
trigger:
  - platform: state
    entity_id: sensor.textbee_device_1_last_incoming_text
condition:
  - condition: template
    value_template: >
      {{ trigger.to_state.state not in ['', 'unknown', 'unavailable'] }}
action:
  - service: logbook.log
    data:
      name: "TextBee SMS"
      message: >
        From {{ states('sensor.textbee_device_1_last_incoming_number') }}:
        {{ states('sensor.textbee_device_1_last_incoming_text') }}
  - service: persistent_notification.create
    data:
      title: "New SMS via TextBee"
      message: >
        From {{ states('sensor.textbee_device_1_last_incoming_number') }}:
        {{ states('sensor.textbee_device_1_last_incoming_text') }}
```

---

# 💬 Example Lovelace “Chat” Card

You can build a simple chat-style UI using an `input_text` for the recipient, an `input_text` for the message, a script, and a small card.

## 1. Helpers (`configuration.yaml`)

```yaml
input_text:
  textbee_chat_recipient:
    name: TextBee Chat Recipient
    icon: mdi:phone
    max: 30

  textbee_chat_message:
    name: TextBee Chat Message
    icon: mdi:message-text
    max: 250
```

## 2. Script to send the message

```yaml
script:
  textbee_send_chat_message:
    alias: TextBee - Send chat message
    mode: single
    sequence:
      - service: textbee.send_sms
        data:
          device_id: "device_1"  # <-- Adjust to your device id
          recipients: "{{ states('input_text.textbee_chat_recipient') }}"
          message: "{{ states('input_text.textbee_chat_message') }}"
      - service: input_text.set_value
        data:
          entity_id: input_text.textbee_chat_message
          value: ""
```

## 3. Lovelace card (simple entities + button)

```yaml
type: vertical-stack
cards:
  - type: custom:button-card
    name: TextBee Chat
    show_state: false
    show_label: true
    icon: mdi:chat-processing
    styles:
      card:
        - padding: 16px
        - border-radius: 16px
      name:
        - font-weight: 600
        - font-size: 18px
      label:
        - white-space: pre-line
        - font-size: 13px
    label: |
      [[[
        const incNum = states['sensor.textbee_device_1_last_incoming_number']?.state || '—';
        const incTxt = states['sensor.textbee_device_1_last_incoming_text']?.state || 'No incoming messages yet';
        const outNum = states['sensor.textbee_device_1_last_outgoing_number']?.state || '—';
        const outTxt = states['sensor.textbee_device_1_last_outgoing_text']?.state || 'No outgoing messages yet';

        return `
Last incoming:
  ${incNum}: ${incTxt}

Last outgoing:
  ${outNum}: ${outTxt}
        `;
      ]]]

  - type: entities
    title: Send SMS via TextBee
    entities:
      - entity: input_text.textbee_chat_recipient
        name: To (phone number)
      - entity: input_text.textbee_chat_message
        name: Message

  - type: button
    name: Send Message
    icon: mdi:send
    tap_action:
      action: call-service
      service: script.textbee_send_chat_message
```

That gives you:

- A “chat header” card showing last in / last out.  
- Two text inputs for number + message.  
- A big **Send** button that calls `textbee.send_sms` via the script.

You can of course wrap this in your Bubble / Mushroom / OnOff styling later – this is just the bare bones.

---

Made by **OnOff Automations**
