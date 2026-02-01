#!/bin/bash
set -e

# =============================================================
# Entrypoint script - Auto-inicializa user_data no primeiro boot
# =============================================================

if [ ! -f "/freqtrade/user_data/config.json" ]; then
    echo "============================================"
    echo ">>> Primeiro boot detectado!"
    echo ">>> Inicializando user_data com defaults..."
    echo "============================================"

    # Criar estrutura de diretorios
    mkdir -p /freqtrade/user_data/strategies
    mkdir -p /freqtrade/user_data/logs
    mkdir -p /freqtrade/user_data/data
    mkdir -p /freqtrade/user_data/models
    mkdir -p /freqtrade/user_data/notebooks

    # Copiar arquivos default
    cp /freqtrade/docker/user_data_default/config.json /freqtrade/user_data/config.json
    cp /freqtrade/docker/user_data_default/strategies/FreqAIStrategy.py /freqtrade/user_data/strategies/FreqAIStrategy.py

    # Gerar JWT key segura automaticamente
    JWT_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/TROCAR_POR_CHAVE_SEGURA_AQUI/$JWT_KEY/" /freqtrade/user_data/config.json

    # Gerar senha aleatoria para API/FreqUI
    API_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
    sed -i "s/TROCAR_SENHA_AQUI/$API_PASS/" /freqtrade/user_data/config.json

    echo "============================================"
    echo ">>> user_data inicializado com sucesso!"
    echo ">>>"
    echo ">>> CREDENCIAIS DA API (FreqUI):"
    echo ">>> Username: freqtrader"
    echo ">>> Password: $API_PASS"
    echo ">>>"
    echo ">>> GUARDE ESTA SENHA!"
    echo ">>> Ela aparece apenas no primeiro boot."
    echo ">>> Para alterar: edite /freqtrade/user_data/config.json"
    echo "============================================"
fi

# Executar o comando passado como argumento
exec "$@"
