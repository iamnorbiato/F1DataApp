# G:\Learning\F1Data\F1Data_App\core\management\commands\check_rc_duplicates_by_category.py
import json
import os
from datetime import datetime
import sys

RACE_CONTROL_JSON_FILE = os.path.join(os.path.dirname(__file__), 'racecontrol.json')

def check_for_duplicates():
    print(f"--- Verificando duplicidades com base em: meeting_key, session_key, date, category ---")

    if not os.path.exists(RACE_CONTROL_JSON_FILE):
        print(f"Erro: Arquivo '{RACE_CONTROL_JSON_FILE}' não encontrado.", file=sys.stderr)
        return

    try:
        with open(RACE_CONTROL_JSON_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Erro ao decodificar JSON: {e}", file=sys.stderr)
        return
    except Exception as e:
        print(f"Erro ao ler o arquivo: {e}", file=sys.stderr)
        return

    if not data:
        print("Aviso: JSON carregado, mas está vazio.")
        return

    print(f"Total de registros carregados: {len(data)}")

    pk_set = set()
    duplicates = []

    for i, record in enumerate(data):
        try:
            meeting_key = record.get("meeting_key")
            session_key = record.get("session_key")
            date_str = record.get("date")
            category = record.get("category") or "null"
            flag = record.get("flag") or  "null"
            sector = record.get("sector") or "null"
            message = record.get("message") or "null"
            driver_number = record.get("driver_number") or "null"
            lap_number = record.get("lap_number") or "null"

            if not all([meeting_key, session_key, date_str]):
                continue

            date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            normalized_date = date_obj.isoformat(timespec='microseconds').replace('+00:00', 'Z')

            composite_key = (meeting_key, session_key, normalized_date, driver_number, lap_number, category, flag, sector )

            if composite_key in pk_set:
                duplicates.append((i + 1, composite_key))
            else:
                pk_set.add(composite_key)

        except Exception as e:
            print(f"Erro ao processar registro {i+1}: {e}", file=sys.stderr)

    print("\n--- Resultado da verificação ---")
    if not duplicates:
        print("✅ Nenhuma duplicidade encontrada com a nova chave (meeting_key, session_key, normalized_date, driver_number, lap_number, category, flag, sector).")
    else:
        print(f"❌ {len(duplicates)} duplicidades encontradas com a chave incluindo category:")
        for idx, dup_key in duplicates[:10]:  # Mostra só os 10 primeiros
            print(f" - Registro #{idx}, chave duplicada: {dup_key}")
        if len(duplicates) > 10:
            print(f"... e mais {len(duplicates) - 10} duplicadas não listadas.")
    print("--------------------------------")

if __name__ == "__main__":
    check_for_duplicates()
