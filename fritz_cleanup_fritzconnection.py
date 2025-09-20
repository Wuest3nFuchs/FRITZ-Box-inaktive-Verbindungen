#!/usr/bin/env python3
"""
fritz_cleanup_fritzconnection.py
Beendet inaktive Verbindungen auf einer FRITZ!Box mit fritzconnection (TR-064).
Nicht alle FRITZ!Box-Modelle stellen eine aktive Verbindungs-Liste zur Verfügung.
Testen Sie zuerst mit DRY_RUN = True.
"""

from fritzconnection import FritzConnection, FritzService
import time
import datetime
import sys

# Konfiguration
FRITZ_HOST = "fritz.box"        # oder IP wie "192.168.178.1"
USERNAME = "yourusername"       # FRITZ!Box-Benutzer mit Berechtigungen
PASSWORD = "yourpassword"
THRESHOLD_SECONDS = 3600 * 24   # z.B. 24 Stunden
DRY_RUN = True
TIMEOUT = 10

def try_service(fc, service_type_fragments):
    """Versucht, einen Service anhand mehrerer Type-Fragmente zu finden."""
    for frag in service_type_fragments:
        try:
            # Suche Service-Objekt durch seinem Namen (fritzconnection kann unterschiedliche Namen verwenden)
            # Prüfen auf existierende Services
            for svcname in fc.services:
                if frag.lower() in svcname.lower():
                    try:
                        return FritzService(fc, svcname)
                    except Exception:
                        continue
        except Exception:
            continue
    return None

def parse_time_str(s):
    """Versuche verschiedene Zeitformate zu parsen, Rückgabe Unix-Timestamp oder None."""
    if s is None:
        return None
    # Wenn bereits int-string
    try:
        return int(s)
    except Exception:
        pass
    fmts = ("%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S")
    for f in fmts:
        try:
            dt = datetime.datetime.strptime(s, f)
            return int(dt.timestamp())
        except Exception:
            continue
    return None

def main():
    try:
        fc = FritzConnection(address=FRITZ_HOST, user=USERNAME, password=PASSWORD, timeout=TIMEOUT)
    except Exception as e:
        print("Verbindung zur FRITZ!Box fehlgeschlagen:", e)
        sys.exit(1)

    # Mögliche Services, die Informationen zu Verbindungen liefern könnten
    candidates = [
        "WANIPConnection", "WANPPPConnection", "ConnectionManager",
        "PPPConnection", "WANCommonInterfaceConfig", "DeviceInfo", "Layer3Forwarding"
    ]

    svc = try_service(fc, candidates)
    if not svc:
        print("Kein passender Service gefunden. Verfügbare Services (Auszug):")
        # Ausgabe einiger Services
        print(", ".join(list(fc.services)[:40]))
        sys.exit(1)

    print("Verwendeter Service:", svc.service_type)

    # Mögliche Actions, die aktive Verbindungen liefern können
    actions_to_try = [
        "GetActiveConnections", "GetGenericConnections", "GetConnectionList",
        "GetActivePortMappings", "GetPortMappingNumberOfEntries"
    ]

    connections = []
    for act in actions_to_try:
        if act in svc.actions:
            try:
                res = svc.call_action(act)
            except Exception:
                continue
            # res kann verschiedene Strukturen haben; wir versuchen typische Felder zu extrahieren
            # Suche rekursiv nach dicts mit sinnvollen Feldern
            def find_conn_nodes(obj):
                found = []
                if isinstance(obj, dict):
                    # Wenn dict Felder enthält, die auf Verbindung hindeuten
                    keys = set(obj.keys())
                    hint_keys = {"RemoteHost", "RemotePort", "Protocol", "BytesSent", "BytesReceived",
                                 "LastActivity", "LastActive", "ConnectionID", "Id", "ID", "State"}
                    if keys & hint_keys:
                        found.append(obj)
                    else:
                        for v in obj.values():
                            found.extend(find_conn_nodes(v))
                elif isinstance(obj, list):
                    for it in obj:
                        found.extend(find_conn_nodes(it))
                return found
            found = find_conn_nodes(res)
            for f in found:
                connections.append(f)
        else:
            # manche Services implementieren Actions nicht sichtbar in actions-Liste; trotzdem versuchen
            try:
                res = svc.call_action(act)
            except Exception:
                continue
            # wie oben
            def find_conn_nodes2(obj):
                found = []
                if isinstance(obj, dict):
                    keys = set(obj.keys())
                    hint_keys = {"RemoteHost", "RemotePort", "Protocol", "BytesSent", "BytesReceived",
                                 "LastActivity", "LastActive", "ConnectionID", "Id", "ID", "State"}
                    if keys & hint_keys:
                        found.append(obj)
                    else:
                        for v in obj.values():
                            found.extend(find_conn_nodes2(v))
                elif isinstance(obj, list):
                    for it in obj:
                        found.extend(find_conn_nodes2(it))
                return found
            found = find_conn_nodes2(res)
            for f in found:
                connections.append(f)

    # Falls nichts gefunden, versuchen wir Port-Mappings (nur zur Info)
    if not connections:
        if "GetPortMappingNumberOfEntries" in svc.actions:
            try:
                n = int(svc.call_action("GetPortMappingNumberOfEntries")["NewPortMappingNumberOfEntries"])
                print("Port-Mapping Einträge:", n)
            except Exception:
                pass
        print("Keine aktiven Verbindungen gefunden oder Service liefert keine Liste.")
        sys.exit(0)

    # Dedup
    unique = []
    seen = set()
    for c in connections:
        key = tuple(sorted([(k, str(v)) for k, v in c.items()]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    connections = unique

    now = time.time()
    to_kill = []

    for c in connections:
        # Versuche verschiedene Felder für Zeitpunkt
        last = None
        for field in ("LastActivity", "LastActive", "LastSeen", "Time", "Timestamp"):
            if field in c:
                last = c.get(field)
                break
        # Falls numeric bytes/age fields vorhanden
        ts = parse_time_str(last)
        # Falls kein Zeitstempel, evtl. Bytes-Transfer seit letzter Aktivität -> überspringen
        if ts is None:
            # Wenn State == 'CLOSED' oder ähnlich, überspringen
            continue
        age = now - ts
        if age >= THRESHOLD_SECONDS:
            to_kill.append((c, ts, int(age)))

    if not to_kill:
        print("Keine inaktiven Verbindungen älter als Schwelle gefunden.")
        sys.exit(0)

    print(f"Zu beendende Verbindungen: {len(to_kill)} (DRY_RUN={DRY_RUN})")
    for c, ts, age in to_kill:
        # Bestimme Identifikatoren zum Schließen
        connid = c.get("ConnectionID") or c.get("Id") or c.get("ID")
        remote = c.get("RemoteHost") or c.get("Description") or ""
        port = c.get("RemotePort") or c.get("Port") or ""
        print(f"- {remote} {port} connid={connid} last={datetime.datetime.fromtimestamp(ts)} age={age}s")

        if not DRY_RUN:
            closed = False
            # Mögliche Close-Actions
            close_actions = ["DeleteConnection", "CloseConnection", "ForceCloseConnection", "DestroyConnection"]
            for action in close_actions:
                if action in svc.actions:
                    try:
                        args = {}
                        if connid:
                            # neue FritzConnection call_action erwartet dict mit Parametern
                            # unterschiedliche Services erwarten unterschiedliche Parameternamen
                            for key in ("ConnectionID", "ID", "Id"):
                                if key in svc.actions.get(action, {}):
                                    args[key] = connid
                            # Wenn keine Parameterbeschreibung vorhanden, senden wir ConnectionID generisch
                            if not args:
                                args = {"ConnectionID": connid}
                        else:
                            # Fallback: RemoteHost/RemotePort falls benötigt
                            if remote:
                                args["RemoteHost"] = remote
                            if port:
                                args["RemotePort"] = port
                        svc.call_action(action, **args)
                        print(f"Action {action} ausgeführt.")
                        closed = True
                        break
                    except Exception as e:
                        # weiter versuchen
                        continue
            if not closed:
                print("Konnte Verbindung nicht schließen: keine passende Aktion oder fehlende Parameter.")

if __name__ == "__main__":
    main()
