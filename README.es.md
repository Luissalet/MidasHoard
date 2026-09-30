# Midas's Hoard

Laboratorio local de investigación de mercados para una sola persona. Congela los datos de mercado en instantáneas con procedencia, guarda tesis de inversión refutables con reglas que las invalidan, ejecuta un backtester honesto con reserva sellada y controles de sobreajuste, y audita cada cifra escrita por un modelo contra la evidencia. Los asistentes lo usan por MCP; tú, desde el navegador.

**Investigación, no trading.** No hay bróker, ni órdenes, ni consejos de comprar o vender. Los resultados son análisis históricos, no asesoramiento; la rentabilidad pasada no garantiza la futura.

## Qué hace

- **Mercado**: busca símbolos, descarga una serie y congélala como instantánea (CSV canónico, sha256, proveedor, momento de descarga, divisa, indicador de ajuste). Compara series con correlación y rendimiento relativo; si difieren la divisa, el ajuste o la frecuencia se rechaza salvo que lo permitas, y toda conversión de divisa queda registrada paso a paso. Los gráficos son SVG dibujados a mano.
- **Tesis**: una afirmación, una hipótesis rival obligatoria, una fecha de corte (`as_of`), un horizonte, evidencia a favor y en contra, y reglas de invalidación en un lenguaje pequeño sin `eval` (`close(AAPL) < 150`, `yoy(CPIAUCSL) > 4`, `drawdown(^GSPC) < -20`, `sma(x,50) < sma(x,200)`). Una regla verdadera invalida la tesis. Las métricas de la evidencia nunca usan datos posteriores al corte. Las comprobaciones son idempotentes y una invalidación emite un único evento.
- **Laboratorio**: estrategias declarativas (JSON, sin código). Una señal en la barra t solo afecta a la posición que rinde en la barra t+1 (lo demuestra un test). Costes, tamaño de posición, rebalanceo, largo/corto, referencia, reserva sellada, walk-forward anclado con rejilla opcional de parámetros, prueba de permutación por bloques, intervalo bootstrap del Sharpe, ajuste de Bonferroni y Sharpe deflactado. Cada variante queda registrada, también los fallos, con aviso de pruebas múltiples.
- **Comité**: dos defensas, un revisor de riesgos independiente y un árbitro sobre un paquete de evidencia fijo. Las citas se validan y cada número pasa por el libro de cifras (observado, derivado, citado, propuesto o sin respaldo). Sin modelo devuelve el paquete de evidencia y métricas deterministas.
- **Cartera**: posiciones desde tabla o CSV, valoración a una fecha, distribución, exposición por divisa, concentración, volatilidad, caída máxima y correlación, con cada conversión registrada.
- **Informes**: Markdown o JSON de una tesis, una ejecución o una cartera, con la procedencia de cada cifra.

## Proveedores de datos

| Proveedor | Datos | Notas |
|---|---|---|
| Yahoo Finance (por defecto) | precios diarios de acciones, ETF, índices, divisas y cripto: `AAPL`, `^GSPC`, `^IBEX`, `SAN.MC`, `EURUSD=X`, `BTC-EUR` | sin clave; endpoint público no oficial, solo uso personal y de investigación; cierres ajustados por splits y dividendos, con los eventos anotados; puede cambiar o limitar sin aviso |
| FRED | series macro | Fuente: Federal Reserve Bank of St. Louis; valores tal como se revisaron, no vintages |
| Banco Central Europeo (European Central Bank) | tipos de referencia del euro y estadísticas (SDMX) | Fuente: ECB Data Portal |
| CoinGecko | precios de cripto, hasta 365 días en el plan gratuito | datos de CoinGecko; con límite de ritmo |
| Alpha Vantage | precios diarios | opcional, desactivado hasta añadir una clave gratuita (`MIDAS_ALPHAVANTAGE_KEY` o Ajustes); barras sin ajustar, cupo diario pequeño |
| Tiingo | precios diarios ajustados, sobre todo EE. UU. | opcional, desactivado hasta añadir una clave gratuita (`MIDAS_TIINGO_KEY` o Ajustes) |
| Stooq | precios al cierre | bloqueado ahora mismo: responde a los scripts con una comprobación de navegador con JavaScript y Midas no intenta saltársela; las descargas fallan con `provider_unavailable` y la fuente aparece como bloqueada |
| CSV | tus propios ficheros | declaras divisa o unidad; no se adivina nada |
| fake | series sintéticas deterministas | para pruebas, demostraciones y uso sin conexión |

Las claves de API introducidas en Ajustes son de solo escritura: se guardan en local y la API solo informa de `configured` y de los cuatro últimos caracteres. Cada proveedor aparece como `ok`, `needs_key` o `blocked` en la página Fuentes y en `market_providers`.

Las respuestas se guardan en caché en disco; con `MIDAS_OFFLINE=1` solo se responde desde la caché. Lee las condiciones de cada proveedor, que aparecen con cada instantánea.

## Ejecución

```
pip install -r requirements.txt
python -m midas_hoard            # http://127.0.0.1:5192
```

El cliente está compilado y versionado en `midas_hoard/static`; para recompilarlo, `npm install && npm run build`. `python scripts/launch.py` busca un puerto libre y abre el navegador; `python scripts/dev.py` arranca la API con recarga y el servidor de desarrollo de Vite.

La configuración va por variables de entorno: `MIDAS_DATA_DIR` (por defecto `./data`), `MIDAS_PORT` (5192), `PORT_STRICT`, `MIDAS_ALLOWED_HOSTS`, `MIDAS_HTTP_TIMEOUT_S`, `MIDAS_CACHE_TTL_S`, `MIDAS_OFFLINE`. Los datos viven en `data/midas.db` (SQLite, WAL) y en `data/snapshots`, `data/runs` y `data/reports`.

## Herramientas para asistentes (MCP)

`mcp_server.py` es un puente stdio. Nunca abre la base de datos: reenvía cada llamada a `POST /api/agent/call` con el token de `data/mcp-token`, lee la lista de herramientas de `GET /api/agent/tools` y arranca la aplicación si nadie responde (`MIDAS_BRIDGE_AUTOSTART=0` lo desactiva). Variables: `MIDAS_URL`, `MIDAS_TOKEN_FILE`, `MIDAS_DATA_DIR`. Los errores conservan su `code` y su `hint`. Los borrados exigen `confirm: true` en la herramienta propietaria. Las 23 herramientas:

### Datos de mercado

| Herramienta | Qué hace |
|---|---|
| `midas_status` | Estado del laboratorio financiero |
| `market_providers` | Proveedores de datos de mercado |
| `market_search` | Buscar símbolos y series |
| `market_fetch` | Descargar datos de mercado |
| `market_series` | Ver una serie |
| `market_compare` | Comparar series |
| `snapshots_list` | Listar datos congelados |

### Tesis

| Herramienta | Qué hace |
|---|---|
| `thesis_create` | Crear tesis de inversión |
| `thesis_get` | Ver una tesis |
| `thesis_list` | Listar tesis |
| `thesis_update` | Editar tesis |
| `thesis_evidence_add` | Añadir evidencia |
| `thesis_check` | Comprobar una tesis |
| `committee_run` | Comité de inversión |

### Laboratorio de estrategias

| Herramienta | Qué hace |
|---|---|
| `strategy_validate` | Validar estrategia |
| `strategy_save` | Guardar estrategia |
| `strategies_list` | Listar estrategias |
| `backtest_run` | Ejecutar backtest |
| `backtest_validate` | Validar backtest |
| `experiments_list` | Registro de experimentos |

### Cartera e informes

| Herramienta | Qué hace |
|---|---|
| `portfolio_set` | Definir cartera |
| `portfolio_analyze` | Analizar cartera |
| `report_export` | Exportar informe |

Referencia completa: [docs/API.md](docs/API.md).

## Método y límites

- Las instantáneas son inmutables y se verifican por hash al cargarlas; los mismos bytes reutilizan la instantánea.
- `as_of` hace los análisis puntuales en el tiempo para los datos que tienes. FRED y otros proveedores publican valores revisados, así que las revisiones aún pueden filtrarse a fechas pasadas; cada instantánea lo indica.
- Las variantes se cuentan por familia de estrategia (hashes de especificación distintos). El Sharpe deflactado necesita al menos 3 variantes.
- Los backtests simulan barras diarias con costes proporcionales: sin ejecución intradía, sin modelo de liquidez, sin impuestos y sin corrección por supervivencia.
- El comité solo es tan bueno como la evidencia que le das; los números sin respaldo se marcan, no se eliminan.
- El histórico de CoinGecko se limita a 365 días en el plan gratuito.

## Pruebas

`python -m pytest -q` (sin red: proveedor falso, transportes HTTP simulados y un modelo guionizado). La integración continua corre en Ubuntu y Windows.

## Licencia

MIT (c) Luis María Salete Cuartero. Los datos siguen sujetos a las condiciones de cada proveedor.
