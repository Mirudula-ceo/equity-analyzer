# Company Research, Important Events & Ranking — original requirement (Tamil)

> Received 19 Oct 2026. Kept verbatim as the source of truth. Implementation status: `docs/COMPANY_RESEARCH_MODULE.md`.
> In this repository the "27 strategies" are the **27 metrics** of `equity/metrics/engine.py`.

---

புரிகிறது. **இது community app requirement இல்லை—நீங்கள் உருவாக்கும் 27-strategy stock analysis algo-வுக்கு மேலாகச் சேர்க்க வேண்டிய “Company Research, Important Events & Ranking” module requirement.**

உங்கள் நோக்கம்: **ஒரு stock எந்த rank-ல் இருக்கிறது என்பதுடன், ஏன் அந்த rank கிடைத்தது, அதன் valuation என்ன, நிறுவனம் அடுத்து என்ன செய்யப்போகிறது என்பதையும் ஆதாரத்துடன் காட்ட வேண்டும்.**

நீங்கள் பேசியதை developer-க்கு கொடுக்கக்கூடிய requirements ஆகக் கீழே consolidate செய்துள்ளேன்.

### 1. NSE நிறுவனங்கள் அனைத்தையும் cover செய்ய வேண்டும்

- NSE-ல் பட்டியலிடப்பட்ட நிறுவனங்களின் பட்டியலை official source மூலம் பெற வேண்டும்.
- புதிய listings, symbol changes, delistings ஆகியவற்றை update செய்ய வேண்டும்.
- ஒவ்வொரு நிறுவனத்திற்கும் தனி company research page இருக்க வேண்டும்.
- **Company count-ஐ 3,099 என்று hard-code செய்யக்கூடாது.** தற்போதைய official list அடிப்படையில் எண்ணிக்கை மாற வேண்டும்.
- Mainboard, SME, ETF போன்ற வகைகளைத் தனியாகக் காட்ட வேண்டும்; strategy analysis-க்கு எந்த வகைகள் eligible என்பதை வரையறுக்க வேண்டும்.

### 2. தகவல் தேட வேண்டிய sources

| Source வகை | எடுக்க வேண்டிய தகவல் |
|---|---|
| **Company official website** | புதிய business, products, brands, expansion plans, future projects |
| **Investor Relations section** | Annual reports, investor presentations, earnings-call transcripts, financial results |
| **NSE/BSE filings** | Material announcements, acquisitions, orders, fundraising, corporate actions |
| **Subsidiary / brand websites** | Parent company website-ல் இல்லாத product மற்றும் business developments |
| **Financial publications** | Bloomberg, Mint, Moneycontrol போன்றவற்றின் relevant reporting |
| **Financial-data platforms** | Tickertape, Screener போன்றவற்றில் கிடைக்கும் financial metrics மற்றும் cross-checks |

**“ஒரு website-ல் இல்லை” என்பதால் “இந்த தகவல் இல்லை” என்று முடிவு செய்யக்கூடாது.** அனுமதிக்கப்பட்ட access, APIs, licences ஆகியவற்றின் அடிப்படையில் source coverage இருக்க வேண்டும்.

### 3. கிடைக்காத தகவலுக்கான research workflow

1. Company identity, symbol, subsidiaries ஆகியவற்றை உறுதி செய்ய வேண்டும்.
2. Official filings மற்றும் financial reports பார்க்க வேண்டும்.
3. Company website, investor presentations, subsidiary/brand websites பார்க்க வேண்டும்.
4. Relevant financial news மற்றும் secondary sources பார்க்க வேண்டும்.
5. கிடைத்த தகவல்களை source, date உடன் இணைக்க வேண்டும்.
6. இன்னும் கிடைக்கவில்லை என்றால் **எங்கு தேடியது, எது கிடைக்கவில்லை** என்பதைச் சொல்ல வேண்டும்.

தகவலின் நிலையை இவ்வாறு வேறுபடுத்த வேண்டும்:

- **Confirmed:** ஆதாரம் கிடைத்துள்ளது.
- **Management guidance:** நிறுவனம் அறிவித்த எதிர்காலத் திட்டம்.
- **Reported / unconfirmed:** செய்தியாக வந்துள்ளது; உறுதி தேவை.
- **Not found:** தேடிய sources-ல் கிடைக்கவில்லை.
- **Not applicable:** அந்த நிறுவனத்திற்கு பொருந்தாது.
- **Conflicting:** Sources ஒன்றுக்கொன்று முரண்படுகின்றன.

**Not found என்பதும் “நிறுவனம் இதைச் செய்யவில்லை” என்பதும் ஒன்றல்ல.**

### 4. முக்கிய business developments capture செய்ய வேண்டும்

ஒவ்வொரு நிறுவனத்திற்கும்:

- புதிய sector/business entry.
- புதிய product அல்லது brand launch.
- EV போன்ற emerging-business plans.
- Acquisitions, mergers, divestments.
- Subsidiaries மற்றும் joint ventures.
- Factory/capacity expansion.
- Capex மற்றும் funding plans.
- பெரிய orders/contracts.
- Regulatory approvals.
- Management changes.
- Debt, default, litigation மற்றும் முக்கிய risks.
- Project delays, cancellations, revised timelines.

உதாரணமாக, ஒரு நிறுவனம் **2028–2030 launch target** அறிவித்திருந்தால்:

> “Management announced a proposed launch during 2028–2030; current execution status: …”

என்று காட்ட வேண்டும். **Future plan-ஐ ஏற்கனவே இயங்கும் business ஆகக் காட்டக்கூடாது.**

### 5. முக்கிய செய்திகளுக்கான நிரந்தர company timeline

**Latest news feed மட்டும் போதாது.** முக்கிய developments பின்னரும் தேடிக் கிடைக்க வேண்டும்.

ஒவ்வொரு event-க்கும்:

| Field | Requirement |
|---|---|
| Event title | என்ன நடந்தது / அறிவிக்கப்பட்டது |
| Category | Acquisition, expansion, product launch போன்றவை |
| Event date | உண்மையில் நிகழ்ந்த அல்லது அறிவிக்கப்பட்ட தேதி |
| Published date | Source வெளியான தேதி |
| First captured | நமது system முதலில் பதிவு செய்த தேதி |
| Source | பெயர் மற்றும் original link |
| Summary | சொந்த வார்த்தைகளில் சுருக்கம் |
| Business relevance | நிறுவனத்திற்கு ஏன் முக்கியம் |
| Timeline | எதிர்பார்க்கப்படும் milestones |
| Status | Announced, underway, completed, delayed, cancelled |
| Last verified | கடைசியாகச் சரிபார்த்த தேதி |

**குறைந்தது 10 ஆண்டுகளுக்கான முக்கிய event history** வைத்திருக்கலாம். Original article நீக்கப்பட்டாலும், அனுமதிக்கப்பட்ட summary, source metadata மற்றும் verification history இருக்க வேண்டும். Full articles சேமிப்பது source rights-ஐப் பொறுத்தது.

ஒரே செய்தியை பல websites வெளியிட்டால் **ஒரே event-ஆக இணைக்க வேண்டும்**. பின்னர் வரும் updates அதே event-க்கு சேர வேண்டும்.

### 6. Valuation மற்றும் rank explanation

ஒவ்வொரு stock-க்கும்:

- தற்போதைய price மற்றும் அதன் timestamp.
- Financial-data period.
- பொருந்தும் valuation metrics.
- Sector/peer comparison.
- 27 strategies-ன் individual results.
- Overall score மற்றும் rank.
- **ஏன் இந்த rank?**
- Positive factors.
- Negative factors / risks.
- Missing-data impact.
- முந்தைய rank-இலிருந்து ஏற்பட்ட மாற்றத்திற்கான காரணம்.

உதாரணம்:

> **Rank 19:** Earnings growth மற்றும் debt metrics score-ஐ உயர்த்தின. அதிக valuation score-ஐக் குறைத்தது. புதிய project management guidance நிலையில் உள்ளது; revenue contribution இன்னும் உறுதி செய்யப்படவில்லை.

**Rank explanation உண்மையான scoring calculation-உடன் match ஆக வேண்டும்.**

### 7. News காட்டுவதுடன் அதன் relevance-ஐ explain செய்ய வேண்டும்

ஒவ்வொரு முக்கிய event-க்கும்:

- எந்த business segment பாதிக்கப்படுகிறது?
- Existing business-ஆ அல்லது proposed business-ஆ?
- Revenue, margin, debt, capex அல்லது risk-க்கு என்ன தொடர்பு?
- Financial impact quantify செய்யப்பட்டுள்ளதா?
- இது ஏற்கனவே 27-strategy metrics-ல் பிரதிபலிக்கிறதா?
- அடுத்து எந்த milestone-ஐ கண்காணிக்க வேண்டும்?

**வெறும் positive news வந்ததால் rank உயரக்கூடாது.** News/event score சேர்க்க வேண்டுமெனில் தனியாக வெளிப்படையான விதிகள் இருக்க வேண்டும். இல்லையெனில் research context ஆக மட்டும் காட்ட வேண்டும்.

### 8. Filters மற்றும் ranking options

- Top 10 / 25 / 50 / 90 / 100 / Custom.
- Overall rank.
- ஒவ்வொரு strategy அடிப்படையிலான rank.
- Sector மற்றும் industry.
- Market capitalisation.
- Valuation range.
- Growth, profitability, debt.
- New business / acquisition / expansion events.
- Event date மற்றும் status.
- Data completeness.
- Last updated date.

**Top 10 என்பது முழு eligible universe-லா அல்லது தேர்ந்தெடுத்த sector-க்குள்ளா என்பதைத் தெளிவாகக் காட்ட வேண்டும்.**

### 9. Company page structure

**Overview · 27 Strategies · Valuation · Business & Future Plans · Important Events · Financials · Risks · Sources**

மேலே ஒரு summary card:

> **Company | Price | Overall Rank | Strategy Score | Data Coverage | Last Updated**

கீழே **“Why this rank?”** மற்றும் **“Important developments”** sections.

### 10. Data updates மற்றும் alerts

- Price, financial results, company announcements ஆகியவற்றிற்கு தனித்தனி update schedules.
- புதிய financial results வந்தால் affected strategy scores மீண்டும் calculate செய்ய வேண்டும்.
- முக்கிய event update வந்தால் timeline refresh செய்ய வேண்டும்.
- Rank change, acquisition, project delay போன்றவற்றிற்கு watchlist alerts.
- Source failure அல்லது stale data இருந்தால் visible status.
- தவறான company-க்கு news இணைக்கப்படாமல் identity checks.
- Historical backtest-ல் **அந்த நேரத்தில் கிடைத்த தகவல் மட்டுமே** பயன்படுத்த வேண்டும்; பின்னர் வெளியான தகவலை பழைய முடிவுகளில் சேர்க்கக்கூடாது.

**Developer scope:**  
**“Existing 27-strategy engine-க்கு மேலாக, NSE-wide company research, source-backed business-development tracking, long-term event history, explainable valuation/ranking, filters, and alerts கொண்ட module உருவாக்க வேண்டும்.”**

27 strategies-ன் exact formulas இந்த message-ல் இல்லை; அவற்றை மாற்றாமல், இந்த module-ஐ மேலதிக requirement ஆக இணைக்க வேண்டும்.