# Protocol evidence

The decoder targets the standalone SCG-1 layout. It does not send queries or
controller commands.

## Manufacturer documentation

- [SCG-1 manual, section 4.3](https://www.innovatemotorsports.com/wp/content/uploads/2022/05/SCG-1-Manual.pdf):
  confirms the four logged channels: wideband, RPM, boost, solenoid duty; use serial
  OUT, with nothing connected to IN for a standalone device.
- [Innovate ISP2 specification, manufacturer document mirrored on Yumpu](https://www.yumpu.com/en/document/view/29377681/innovate-serial-protocol-2-innovate-motorsports):
  19200 baud, 8N1, big-endian words, nominal 81.92 ms packets.
- [Innovate OT-2 SDK, pages 14–15, manufacturer document mirror](https://www.yumpu.com/en/document/view/40253169/ot-2-sdk-manual-innovate-motorsports/15):
  data/response distinction and aux/new-lambda channel layouts.
- [SDK pages 16–17](https://www.yumpu.com/en/document/view/40253169/ot-2-sdk-manual-innovate-motorsports/16):
  lambda status, fuel multiplier, and invalid-state handling.
- [Serial2 supplement](https://www.yumpu.com/en/document/view/29186219/serial2-protocol-supplement-introduction-innovate-motorsports):
  configuration/program modes are separate from normal streaming. This app never
  enters them or sends query, reset, or logging-control commands.

Legacy official Seriallog-2.pdf / OT-2 SDK download links failed or returned web
challenge pages during inspection. Those responses are not protocol PDFs.

## Implemented standalone profile

Header mask `0xA280`; bit 12 denotes a data packet. Payload length in words is
`((header_high & 1) << 7) | (header_low & 127)`. The observed SCG-1 header is `B2 85`:
five payload words, twelve total bytes. Data: two-word new-lambda channel, then
RPM, boost, duty. Nonmatching lengths/layouts produce unavailable values.
The parser accepts split chunks and multiple packets, rejects invalid payload
high bits, and resynchronizes after garbage or truncated packets. No checksum
exists in this stream; structural checks cannot detect every corrupted reading.

Lambda status is `(status_high >> 2) & 7`. Only zero permits numeric lambda/AFR.
The lambda value removes the intervening reserved bit: `L=(high<<7)|low`;
lambda is `L/1000 + 0.5`. Fuel multiplier uses the low bit of status_high and
seven bits of status_low, divided by ten. AFR is lambda × that multiplier.
The full lambda value is retained, including lean readings above a gauge's normal
plot range. Error codes, warmup, and oxygen-content states remain statuses.

Aux values use `(high<<7)|low`, restricted to 0..1023. RPM is deliberately ignored.

## SCG-1 scale evidence from installed LogWorks

The general protocol does not specify per-device aux units. Offline metadata/IL
inspection of the installed `C:\Program Files (x86)\LogWorks3\mts.dll` found:

- `AddPhysDevice` identifies `SCG1` and dispatches to `AddBCG1Device`.
- That definition lists AFR, RPM, BOOST, DUTY in order.
- BOOST endpoints are **−14.5..43.5 PSI**; DUTY endpoints are **0..100%**.
- These give `boost_psi = raw * 58 / 1023 - 14.5`,
  `commanded_wgdc_pct = raw * 100 / 1023`, using the SDK's aux endpoint convention.

DLL SHA-256: `1f11c2791cca5f208a4512dd9b02c2ee8aa302189b99d1cdab7a01b8870b4e06`.
The DLL was parsed offline, not executed or redistributed, and is not an app
dependency. Inspection scripts require optional `dnfile` and `dncil` packages.

Recorded example: `B2 85 43 13 17 63 00 00 02 03 00 00`.
This decodes to lambda 3.543, AFR 52.0821, boost 0.184262 psi, duty 0%.
A bench comparison agreed with the gauge at approximately 52.8–53 AFR.
Nonzero WGDC and pressure across the operating range still need a reference
comparison.
