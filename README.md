# FRITZ-Box-inaktive-Verbindungen
Python-Skript: inaktive Verbindungen auf einer FRITZ!Box per TR-064 beenden


Hinweis: Das Skript nutzt TR-064 (UPnP) zur Abfrage der aktuellen Verbindungen und beendet solche, die länger inaktiv sind. Es setzt voraus, dass TR-064 auf der FRITZ!Box aktiviert ist (Heimnetz → Heimnetzübersicht → Netzwerkeinstellungen → Heimnetzfreigaben / FRITZ!Box-Benutzer mit Berechtigung für Fernzugriff). Teste vorsichtig — unerwartetes Beenden kann laufende Verbindungen stören.

Installation (einmalig)

    Python 3.8+
    Abhängigkeiten installieren:
    pip install requests xmltodict


Script (speichern als fritz_cleanup.py). Passe die Variablen user/psw/host und THRESHOLD_SECONDS an.

#!/usr/bin/env python3
"""
fritz_cleanup.py
Beendet inaktive Verbindungen auf einer FRITZ!Box via TR-064 (WANIPConnection / Layer3Forwarding)
Vorgehensweise:
 - Holt die aktuelle NAT/Port-Mapping/Connection-Liste (je nach FRITZ!Box-API)
 - Beendet Verbindungen, deren letzte Aktivität älter ist als THRESHOLD_SECONDS

WARNUNG: AVM TR-064-Implementierungen unterscheiden sich. Teste zuerst mit DRY_RUN = True.
"""

import requests
import xmltodict
import time
from urllib.parse import urljoin
import sys

# Konfiguration
FRITZ_HOST = "http://fritz.box"    # oder z.B. "http://192.168.178.1"
USERNAME = "yourusername"          # FRITZ!Box-Benutzername (falls nötig)
PASSWORD = "yourpassword"          # Passwort
THRESHOLD_SECONDS = 3600 * 24      # Verbindungen älter als 24h beenden
DRY_RUN = True                     # True = nur anzeigen, False = wirklich beenden
TIMEOUT = 10

# Hilfsfunktionen
def get_device_desc_url(base_url):
    """Hole device description (root) URL für TR-064 (urn:schemas-upnp-org:device:InternetGatewayDevice:1)."""
    # Standard-URL für TR-064: base_url + ":49000/tr64desc.xml" bei manchen Geräten; fritz.box antwortet oft auf base root
    candidates = [
        urljoin(base_url, ":49000/tr64desc.xml"),
        urljoin(base_url, "/tr64desc.xml"),
        urljoin(base_url, "/tr64desc.xml"),
        urljoin(base_url, "/igddesc.xml"),
        urljoin(base_url, "/"),
    ]
    for u in candidates:
        try:
            r = requests.get(u, timeout=TIMEOUT, auth=(USERNAME, PASSWORD))
            if r.status_code == 200 and "xml" in r.headers.get("Content-Type", ""):
                return u, r.text
        except requests.RequestException:
            continue
    raise RuntimeError("Device description konnte nicht abgerufen werden. Prüfe FRITZ_HOST und Zugangsdaten.")

def find_service_control_url(desc_xml, service_type_fragment):
    """Parst device description XML und sucht nach einem service mit bestimmtem Type-Fragment."""
    doc = xmltodict.parse(desc_xml)
    # Suche rekursiv nach serviceList
    def find_services(node):
        services = []
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "serviceList":
                    sl = v.get("service") if isinstance(v, dict) else v
                    if sl:
                        if isinstance(sl, list):
                            services.extend(sl)
                        else:
                            services.append(sl)
                else:
                    services.extend(find_services(v))
        elif isinstance(node, list):
            for it in node:
                services.extend(find_services(it))
        return services
    services = find_services(doc)
    for s in services:
        stype = s.get("serviceType", "")
        if service_type_fragment in stype:
            control = s.get("controlURL")
            if control:
                return control, stype
    return None, None

def soap_request(control_url, service_type, action, arguments=None):
    """Sendet SOAP-Action an control_url (vollständige URL benötigt)."""
    if arguments is None:
        arguments = {}
    # Vollständige URL
    if control_url.startswith("http"):
        url = control_url
    else:
        # base = FRITZ_HOST (mit Schema)
        base = FRITZ_HOST
        url = urljoin(base, control_url)
    # SOAP-Body
    args_xml = "".join(f"<{k}>{v}</{k}>" for k, v in arguments.items())
    body = f"""<?xml version="1.0"?>
    <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
      <s:Body>
        <u:{action} xmlns:u="{service_type}">
          {args_xml}
        </u:{action}>
      </s:Body>
    </s:Envelope>"""
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPACTION": f'"{service_type}#{action}"'
    }
    r = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=TIMEOUT, auth=(USERNAME, PASSWORD))
    r.raise_for_status()
    return r.text

def parse_connections_from_url_list(text):
    """Versuch, Verbindungsdaten über ConnectionManager oder Layer3Forwarding auszulesen.
    AVM hat unterschiedliche Services; wir versuchen mehrere Actions.
    """
    # Einfacher Ansatz: Abfrage der UDP/TCP-Connections via "GetGenericPortMappingEntry" nicht für aktive sessions.
    # Besser: FRITZ!Box bietet ConnectionManager / WANIPConnection - GetStatusInfo oder GetActiveConnections (nicht standard)
    # Wir versuchen eine generische Action 'GetConnectionList' / 'GetActiveConnections' falls vorhanden.
    return None

def main():
    print("Verbinde mit FRITZ!Box...")
    desc_url, desc_xml = get_device_desc_url(FRITZ_HOST)
    control_url, service_type = find_service_control_url(desc_xml, "WANIPConnection")
    if not control_url:
        control_url, service_type = find_service_control_url(desc_xml, "WANPPPConnection")
    if not control_url:
        # alternativ ConnectionManager / Layer3Forwarding
        control_url, service_type = find_service_control_url(desc_xml, "ConnectionManager")
    if not control_url:
        print("Kein passender TR-064 Service gefunden (WANIPConnection/WANPPPConnection/ConnectionManager).")
        sys.exit(1)
    print(f"Gefundener Service: {service_type}, ControlURL: {control_url}")

    # Beispiel: versuche GetActiveConnections (nicht standard) — viele FRITZ!Boxen bieten spezielle Actions
    try:
        # Prüfe ob Action GetActiveConnections existiert, sonst weiter
        resp = soap_request(control_url, service_type, "GetActiveConnections")
        doc = xmltodict.parse(resp)
        # Erwartetes Format variiert; hier versuchen wir generisch nach 'Connection' Einträgen zu suchen
        conns = []
        # Suche nach allen 'Connection' Knoten
        def find_nodes(node, name):
            found = []
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == name:
                        if isinstance(v, list):
                            found.extend(v)
                        else:
                            found.append(v)
                    else:
                        found.extend(find_nodes(v, name))
            elif isinstance(node, list):
                for it in node:
                    found.extend(find_nodes(it, name))
            return found
        conn_nodes = find_nodes(doc, "Connection")
        for c in conn_nodes:
            # Mögliche Felder: RemoteHost, Protocol, Port, BytesSent, BytesReceived, LastActivity, State
            last = c.get("LastActivity") or c.get("LastActive") or c.get("LastSeen")
            name = c.get("RemoteHost") or c.get("Description") or str(c)
            conns.append({"raw": c, "name": name, "last": last})
    except Exception as e:
        print("GetActiveConnections fehlgeschlagen:", e)
        conns = []

    if not conns:
        print("Keine aktiven Verbindungen gefunden oder API unsupported. Einige FRITZ!Box-Modelle erlauben solche Abfragen nicht.")
        sys.exit(0)

    # Verbindungen auswählen, die älter als THRESHOLD sind
    to_kill = []
    now = time.time()
    for c in conns:
        last = c["last"]
        # Versuche, LastActivity als Unix-Timestamp zu interpretieren, sonst parse heuristisch
        ts = None
        if last is None:
            # Wenn kein LastActivity vorhanden, ignoriere
            continue
        try:
            ts = int(last)
        except Exception:
            # Versuche gängiges Format "YYYY-MM-DDTHH:MM:SS" oder "DD.MM.YYYY HH:MM:SS"
            fmt_ts = None
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    import datetime
                    dt = datetime.datetime.strptime(last, fmt)
                    ts = dt.timestamp()
                    break
                except Exception:
                    continue
        if ts is None:
            continue
        age = now - ts
        if age >= THRESHOLD_SECONDS:
            to_kill.append((c, int(ts), int(age)))

    if not to_kill:
        print("Keine inaktiven Verbindungen älter als Schwelle gefunden.")
        sys.exit(0)

    print(f"Gefundene zu beendende Verbindungen: {len(to_kill)} (DRY_RUN={DRY_RUN})")
    for c, ts, age in to_kill:
        name = c["name"]
        print(f"- {name}, last={ts}, age={age}s")
        if not DRY_RUN:
            # Versuch eine DeleteConnection oder CloseConnection Action
            tried = False
            for action in ("DeleteConnection", "CloseConnection", "ForceCloseConnection", "DestroyConnection"):
                try:
                    # Möglicherweise benötigt die Action Argumente wie ConnectionID
                    connid = c["raw"].get("ConnectionID") or c["raw"].get("Id") or c["raw"].get("ID")
                    args = {}
                    if connid:
                        args_key = "ConnectionID" if "ConnectionID" in c["raw"] else "ID"
                        args[args_key] = connid
                    # Falls kein ID bekannt, versuche mit RemoteHost/Port
                    if not args:
                        if "RemoteHost" in c["raw"]:
                            args["RemoteHost"] = c["raw"]["RemoteHost"]
                        if "RemotePort" in c["raw"]:
                            args["RemotePort"] = c["raw"]["RemotePort"]
                    resp = soap_request(control_url, service_type, action, args)
                    print(f"Action {action} erfolgreich: {resp[:200]}...")
                    tried = True
                    break
                except Exception as e:
                    # ignore und weiter versuchen
                    continue
            if not tried:
                print("Keine passende Aktion gefunden, konnte Verbindung nicht beenden.")

if __name__ == "__main__":
    main()



#Erläuterungen / Tipps

    Viele FRITZ!Box-Modelle bieten keine standardisierte API für aktive Sitzungsliste; AVM benutzt oft proprietäre Actions. Falls GetActiveConnections fehlschlägt, muss die spezifische Service/Action der Box untersucht werden.
    Setze DRY_RUN = True beim ersten Test, dann auf False, wenn alles wie gewünscht funktioniert.
    Für robustere Steuerung kann das Script mit speziellen fritzconnection-Bibliotheken (z. B. python-fritzconnection) ersetzt werden:

    pip install fritzconnection
