# План рефакторинга: Выделение visual.js из app.js

## Цель

Создать отдельный `web/visual.js` для страницы `/visual`, изолировав логику визуального просмотра сделки от основного интерфейса. Это устранит зависимость visual mode от DOM-элементов, существующих только в `index.html`.

## Анализ: Что нужно для visual mode

### Функции ИЗ app.js (нужны в visual.js):

| Функция | Назначение | Сложность |
|---------|-----------|-----------|
| `parseTradeVisualParams()` | Парсинг URL params | Низкая |
| `initVisualMode()` | Инициализация visual mode | Низкая |
| `showTradeInfoBanner()` | Показать баннер сделки | Низкая |
| `autoStartReplay()` | Создание и запуск реплея | Средняя |
| `initChart()` | Инициализация H1 графика | Низкая |
| `initDetailChart()` | Инициализация детального графика | Низкая |
| `syncChartToCursor()` | Синхронизация H1 графика с курсором | Средняя |
| `renderLevelLines()` | Отрисовка уровней на H1 | Средняя |
| `renderDetailLevelLines()` | Отрисовка уровней на детальном | Средняя |
| `renderTradeLevels()` | Отрисовка Entry/SL/TP/Trail/BE линий | Средняя |
| `syncDetail()` | Загрузка детальных свечей | Средняя |
| `animateDetailCandles()` | Анимация детальных свечей | Средняя |
| `autoPlayStep()` | Шаг автовоспроизведения | Средняя |
| `command()` | Обработка команд (play/pause/step/reset) | Низкая |
| `render()` | Обновление UI из snapshot | Средняя |
| `checkServer()` | Проверка статуса сервера | Низкая |
| `showHint()` / `hideHint()` | Показ подсказок | Низкая |
| `stopAnimation()` / `startAutoPlay()` | Управление анимацией | Низкая |
| `resetDetailChart()` | Сброс детального графика | Низкая |
| `destroyDetailChart()` | Удаление детального графика | Низкая |
| `clearLevelLines()` / `clearDetailLevelLines()` / `clearTradeLevelLines()` | Очистка линий | Низкая |
| `setPivotMarkers()` | Маркеры пивотов | Низкая |
| `scrollChartToRight()` / `enablePriceAutoScale()` | Утилиты графика | Низкая |
| `fmtPrice()` / `utcFormat()` | Форматирование | Низкая |
| `request()` | HTTP утилита | Низкая |

### Функции НЕ нужны в visual.js:

| Функция | Причина исключения |
|---------|-------------------|
| `loadInstruments()` | Работа с формой symbol select |
| `selectedConfirmationMethods()` | Работа с radio buttons |
| `applyTradeVisualParams()` | Только для index.html |
| `showTradeVisualIndicator()` | Только для index.html |
| `clearTradeVisualMode()` | Только для index.html |
| `findActiveSetup()` | Не используется в visual mode (trade уровни статичны) |
| `setupDetailStatus()` | Не используется в visual mode |
| `showResolution()` | Не используется в visual mode |
| `barConfirms()` | Не используется в visual mode |
| `drawSetupMarkers()` | Не используется (setups не создаются в visual mode) |
| `openDetailPanel()` | Объединена с initDetailChart |
| `stopDetailAnimation()` | Объединена с resetDetailChart |
| `clearDetailViewData()` | Не нужна |

### State (из app.js state object):

```javascript
// Нужно в visual.js:
const state = {
  runId: null,
  chart: null,
  candleSeries: null,
  allCandles: [],
  animTimer: null,
  animRunning: false,
  lastStatus: null,
  drawnTime: undefined,
  chartHasData: false,
  levelLines: [],
  totalBars: 0,
  displayFromMs: null,
  detailChart: null,
  detailSeries: null,
  detailActive: false,
  detailEnabled: false,
  detailPrevMs: null,
  detailDrawnTime: undefined,
  detailAnimSeq: 0,
  detailCount: 0,
  detailBars: [],
  detailLevelLines: [],
  tradeLevelLines: [],          // НОВОЕ: линии Entry/SL/TP
  pricePrecision: 2,
  tickSize: '0.01',
  tradeVisualParams: null,       // НОВОЕ: параметры сделки из URL
};
```

---

## Пошаговый план реализации

### Этап 1: Подготовка (30 мин)

- [ ] 1.1 Создать файл `web/visual.js`
- [ ] 1.2 Скопировать константы и утилиты из `app.js`:
  - `RIGHT_OFFSET_BARS`
  - `$()` helper
  - `fmtPrice()`, `utcFormat()`, `scrollChartToRight()`, `enablePriceAutoScale()`
  - `request()` fetch wrapper
- [ ] 1.3 Создать упрощённый `state` объект (только для visual mode)

### Этап 2: Парсинг и инициализация (1 час)

- [ ] 2.1 Скопировать `parseTradeVisualParams()` из `app.js`
- [ ] 2.2 Создать `showTradeInfoBanner(params)` — показать баннер из URL params
- [ ] 2.3 Создать `initVisualMode(params)`:
  - Скрыть `control-panel-minimal` (он не нужен)
  - Показать `trade-info` banner
  - Запустить `autoStartReplay(params)`
- [ ] 2.4 Создать `autoStartReplay(params)`:
  - Использовать `params.detail_tf` или '5m' по умолчанию
  - Использовать `params.confirmation_method` из URL
  - Использовать `params.display_from` или текущую дату

### Этап 3: Графики и уровни (2 часа)

- [ ] 3.1 Скопировать `initChart()` и `initDetailChart()` из `app.js`
- [ ] 3.2 Скопировать `destroyDetailChart()` и `resetDetailChart()`
- [ ] 3.3 Скопировать `renderTradeLevels(params)` — отрисовка Entry/SL/TP/Trail/BE
- [ ] 3.4 Скопировать `renderLevelLines()` и `renderDetailLevelLines()`
- [ ] 3.5 Скопировать `clearLevelLines()`, `clearDetailLevelLines()`, `clearTradeLevelLines()`
- [ ] 3.6 Скопировать `setPivotMarkers()` и `syncChartToCursor()`

### Этап 4: Детальные свечи (1 час)

- [ ] 4.1 Скопировать `syncDetail()` и `animateDetailCandles()`
- [ ] 4.2 Адаптировать логику триггера открытия детального графика:
  - В visual mode детальный график открывается при достижении цены ±0.5% от Entry
  - (Убрать логику touch/approaching для уровней — в visual mode нет уровней)

### Этап 5: Управление replay (1 час)

- [ ] 5.1 Скопировать `render()` — упрощённая версия (без формы)
- [ ] 5.2 Скопировать `checkServer()`, `showHint()`, `hideHint()`
- [ ] 5.3 Скопировать `startAutoPlay()` и `stopAnimation()`
- [ ] 5.4 Скопировать `autoPlayStep()` — адаптировать:
  - Убрать логику touch/approaching level events
  - Добавить проверку достижения Entry price (±0.5%)
  - Добавить проверку достижения SL/TP
  - Добавить логику остановки при закрытии сделки
- [ ] 5.5 Скопировать `command()` — упрощённая (без проверки confirmation methods)

### Этап 6: Обновление HTML (30 мин)

- [ ] 6.1 В `visual.html`:
  - Заменить `<script src="/static/app.js">` на `<script src="/static/visual.js">`
  - Убрать inline-скрипт парсинга URL params (теперь в visual.js)
  - Убрать `?v=1.0.3` или добавить динамическую версию из `/api/version`
- [ ] 6.2 В `app.py` (опционально):
  - Добавить эндпоинт `/api/version` если его нет (уже есть на строке 477)

### Этап 7: Рефакторинг app.js (30 мин)

- [ ] 7.1 Удалить из `app.js` функции, перенесённые в `visual.js`
- [ ] 7.2 Удалить `tradeVisualParams` и связанные функции:
  - `parseTradeVisualParams()`
  - `applyTradeVisualParams()`
  - `showTradeVisualIndicator()`
  - `clearTradeVisualMode()`
  - `renderTradeLevels()`
  - `initVisualMode()`
  - `showTradeInfoBanner()`
  - `autoStartReplay()`
- [ ] 7.3 Удалить из `state` объекта:
  - `tradeLevelLines`
  - `tradeVisualParams`
  - `pendingTradeSymbol` (если был)

### Этап 8: Тестирование (1 час)

- [ ] 8.1 Проверить `/visual` — открывается без ошибок
- [ ] 8.2 Проверить `/visual?symbol=BTCUSDT&entry_price=...` — баннер отображается
- [ ] 8.3 Проверить `/visual` — replay стартует автоматически
- [ ] 8.4 Проверить `/visual` — детальный график открывается при приближении к Entry
- [ ] 8.5 Проверить `/visual` — линии Entry/SL/TP рисуются
- [ ] 8.6 Проверить `/visual` — replay останавливается при достижении SL/TP
- [ ] 8.7 Проверить `/index` — всё работает как раньше
- [ ] 8.8 Проверить `/backtest` — всё работает как раньше
- [ ] 8.9 Запустить `pytest tests/` — все тесты проходят

---

## Структура файла visual.js (ожидаемая)

```javascript
// web/visual.js
// Visual mode: отдельный файл для просмотра сделки из backtester

'use strict';

// === Константы ===
const RIGHT_OFFSET_BARS = 5;
const $ = (id) => document.getElementById(id);

// === State ===
const state = {
  runId: null,
  chart: null,
  candleSeries: null,
  allCandles: [],
  animTimer: null,
  animRunning: false,
  lastStatus: null,
  drawnTime: undefined,
  chartHasData: false,
  levelLines: [],
  totalBars: 0,
  displayFromMs: null,
  detailChart: null,
  detailSeries: null,
  detailActive: false,
  detailEnabled: false,
  detailPrevMs: null,
  detailDrawnTime: undefined,
  detailAnimSeq: 0,
  detailCount: 0,
  detailBars: [],
  detailLevelLines: [],
  tradeLevelLines: [],
  pricePrecision: 2,
  tickSize: '0.01',
  tradeVisualParams: null,
};

// === Утилиты ===
// fmtPrice, utcFormat, scrollChartToRight, enablePriceAutoScale, request, ...

// === Парсинг URL params ===
function parseTradeVisualParams() { /* ... */ }

// === Инициализация ===
function initVisualMode(params) { /* ... */ }
function showTradeInfoBanner(params) { /* ... */ }
function autoStartReplay(params) { /* ... */ }

// === Графики ===
function initChart() { /* ... */ }
function initDetailChart() { /* ... */ }
function destroyDetailChart() { /* ... */ }
function resetDetailChart() { /* ... */ }

// === Отрисовка уровней ===
function clearLevelLines() { /* ... */ }
function clearDetailLevelLines() { /* ... */ }
function clearTradeLevelLines() { /* ... */ }
function renderTradeLevels(params) { /* ... */ }
function renderLevelLines(levels) { /* ... */ }
function renderDetailLevelLines(levels) { /* ... */ }
function setPivotMarkers(markers) { /* ... */ }

// === Синхронизация ===
function syncChartToCursor(snapshot) { /* ... */ }
function syncDetail(snapshot) { /* ... */ }
function animateDetailCandles(candles, startMs, endMs) { /* ... */ }

// === Управление replay ===
function render(snapshot) { /* ... */ }
function checkServer() { /* ... */ }
function showHint(text, isFinish) { /* ... */ }
function hideHint() { /* ... */ }
function stopAnimation() { /* ... */ }
function startAutoPlay() { /* ... */ }
async function autoPlayStep() { /* ... */ }
async function command(name) { /* ... */ }

// === Инициализация при загрузке ===
async function init() {
  checkServer();
  
  const params = parseTradeVisualParams();
  if (!params) {
    // Показать сообщение, что нужен symbol параметр
    return;
  }
  
  state.tradeVisualParams = params;
  initVisualMode(params);
}

// Запуск
document.addEventListener('DOMContentLoaded', init);
```

---

## Файлы для изменения

| Файл | Действие |
|------|----------|
| `web/visual.js` | **Создать** — новый файл |
| `web/visual.html` | Изменить — заменить app.js на visual.js, убрать inline script |
| `web/app.js` | Изменить — удалить перенесённые функции |
| `web/styles.css` | Возможно не требует изменений |

---

## Риски и edge-cases

| Риск | Вероятность | Влияние | Митигация |
|------|-----------|---------|-----------|
| Ошибки при копировании функций | Средняя | Высокое | Тщательное тестирование |
| Расхождение в поведении visual mode | Средняя | Среднее | Сравнить с текущей реализацией |
| API endpoint `/api/version` недоступен | Низкая | Низкое | Fallback на версию из VERSION файла |
| URL params имеют неверный формат | Средняя | Среднее | Валидация в `parseTradeVisualParams()` |

---

## Оценка времени

| Этап | Время |
|------|-------|
| Этап 1: Подготовка | 30 мин |
| Этап 2: Парсинг и инициализация | 1 час |
| Этап 3: Графики и уровни | 2 часа |
| Этап 4: Детальные свечи | 1 час |
| Этап 5: Управление replay | 1 час |
| Этап 6: Обновление HTML | 30 мин |
| Этап 7: Рефакторинг app.js | 30 мин |
| Этап 8: Тестирование | 1 час |
| **Итого** | **~8 часов** |

---

## Критерии завершения

1. `/visual` открывается без ошибок в консоли
2. `/visual?symbol=BTCUSDT&entry_price=50000&stop_price=49000&take_price=52000` показывает баннер и запускает replay
3. Детальный график открывается автоматически при приближении к Entry price
4. Все три линии (Entry, SL, TP) отображаются на H1 графике
5. `/index` и `/backtest` работают без изменений
6. `pytest tests/` проходят без ошибок
