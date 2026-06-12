"use client";

import { useEffect, useState } from "react";
import QRCode from "qrcode";
import type { DeviceMqttProvisioningBundle } from "@/lib/deviceApi";
import { stringifyMqttProvisioningQrPayload } from "@/lib/mqttProvisioning";

interface MqttProvisioningQrProps {
  provisioning: DeviceMqttProvisioningBundle;
}

export function MqttProvisioningQr({ provisioning }: MqttProvisioningQrProps) {
  const [qrMarkup, setQrMarkup] = useState<string>("");

  const qrPayload = stringifyMqttProvisioningQrPayload(provisioning);

  useEffect(() => {
    let active = true;

    void QRCode.toString(qrPayload, {
      errorCorrectionLevel: "M",
      margin: 1,
      type: "svg",
      width: 220,
      color: {
        dark: "#0f172a",
        light: "#ffffff",
      },
    })
      .then((markup) => {
        if (active) {
          setQrMarkup(markup);
        }
      })
      .catch(() => {
        if (active) {
          setQrMarkup("");
        }
      });

    return () => {
      active = false;
    };
  }, [qrPayload]);

  return (
    <div className="rounded-[1.5rem] border border-slate-200 bg-white px-4 py-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-slate-900">Provisioning QR</p>
          <p className="mt-1 text-sm text-slate-600">
            Scan this one-time bundle into the device setup flow. It includes broker host, port 1883, identity, password, telemetry/status publish topics, and command/config/OTA subscribe topics.
          </p>
        </div>
      </div>
      <div className="mt-4 flex justify-center">
        <div
          aria-label="MQTT provisioning QR code"
          className="flex min-h-[236px] min-w-[236px] items-center justify-center rounded-2xl border border-slate-200 bg-slate-50 p-3"
          data-qr-payload={qrPayload}
          data-testid="mqtt-provisioning-qr"
        >
          {qrMarkup ? (
            <div
              className="h-[220px] w-[220px]"
              dangerouslySetInnerHTML={{ __html: qrMarkup }}
            />
          ) : (
            <span className="text-xs text-slate-500">Generating QR...</span>
          )}
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-500">
        This QR is only available in the immediate onboarding success state. Shivex will not show it again after this screen is closed or refreshed.
      </p>
    </div>
  );
}
