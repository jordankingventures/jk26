# Global Charts bijwerken (handmatig, via Safari)

Het automatisch ophalen krijgt sinds okt 2026 een HTTP 429-fout van YouTube. In
Safari werkt het wel (niet in Chrome). Zo werk je de officiële totalen bij:

## Eenmalig

Safari → Instellingen → **Geavanceerd** → vink **"Toon functies voor webontwikkelaars"** aan.

## Elke keer

1. Open in **Safari** een artiestpagina, bijvoorbeeld:
   https://charts.youtube.com/artist/%2Fm%2F0gjdn4c
2. Open de console: **Cmd + Option + C** (of menu Ontwikkel → Toon JavaScript-console).
3. Kopieer de code hieronder, plak die in de console (Cmd + V) en druk op **Enter**.
   Typt Safari niets of vraagt het om toestemming om te plakken? Sta het dan toe en probeer opnieuw.
4. Wacht ~10 seconden op de melding **"Klaar: 16 artiesten tot en met …"**.
   Er staat nu een **`gc.json`** in je map Downloads (sta de download toe als Safari erom vraagt).
   De JSON staat ook op je klembord.
5. Upload `gc.json` hier, in deze map (`data/incoming`) op github.com:
   **Add file → Upload files** → sleep `gc.json` erin → **Commit changes** (rechtstreeks naar `main`).
   Gebruik gerust elke keer dezelfde bestandsnaam: het bestand wordt na verwerking weer weggehaald.
6. Na ongeveer een minuut: kijk op het tabblad **Actions** of de run
   **"Verwerk Global Charts-upload"** groen is. Dan staat de nieuwe data erin.
   Is de run rood, dan is het bestand afgekeurd (reden staat in de run, bv. kapotte download):
   haal het opnieuw op en upload opnieuw.

Plak nooit cookies of headers ergens; alleen dit bestand is nodig.

## De code om te kopiëren (stap 3)

```js
(async()=>{const MELD=m=>{console.log(m);alert(m)};const ids={"taylor":"/m/0dl567","drake":"/m/05mt_q","wallen":"/g/11g7ntnqs5","badbunny":"/g/11gdq15782","ariana":"/m/09gkdy4","youngboy":"/g/11g9nh3b7y","bieber":"/m/06w2sn5","fuerzaregida":"/g/11fy19q209","future":"/m/0hhwdgn","kanye":"/m/02l840","katseye":"/g/11vj6xh7_6","kendrick":"/m/0g9x698","olivia":"/g/11j0_8y5xw","postmalone":"/g/11bw82cs0m","tatemcrae":"/g/11cs4tyyr8","weeknd":"/m/0gjdn4c"};const ctx={client:{clientName:"WEB_MUSIC_ANALYTICS",clientVersion:"2.0",hl:"en",gl:"US",experimentIds:[],experimentsToken:"",theme:"MUSIC"}};const day=n=>new Date(Date.now()-n*864e5).toISOString().slice(0,10);const wacht=ms=>new Promise(f=>setTimeout(f,ms));const q=(id,end)=>fetch("/youtubei/v1/browse?alt=json",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({context:ctx,browseId:"FEmusic_analytics_insights_artist",query:"perspective=ARTIST&entity_params_entity=ARTIST&artist_params_id="+encodeURIComponent(id)+"&date_params_start_time="+day(18)+"T00:00:00Z&date_params_end_time="+end+"T00:00:00Z&date_params_interval=DAY"})});let end=null;for(let n=0;n<=4;n++){const e=day(n);const r=await q(ids.taylor,e);if(r.ok){end=e;break}await wacht(500)}if(!end){MELD("Geen einddatum werkt (alle verzoeken geweigerd). Open eerst een artiestpagina op charts.youtube.com in Safari.");return}const out={},fouten=[];for(const[naam,id]of Object.entries(ids)){const r=await q(id,end);if(!r.ok){fouten.push(naam+" ("+r.status+")");continue}const d=await r.json();const dv=d.contents.sectionListRenderer.contents[0].musicAnalyticsSectionRenderer.content.dates[0].dateViews;out[naam]=Object.fromEntries(dv.map(x=>[x.date,+x.viewCount]));await wacht(500)}const tekst=JSON.stringify(out);let kopie=false;try{copy(tekst);kopie=true}catch(e){}try{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([tekst],{type:"application/json"}));a.download="gc.json";document.body.appendChild(a);a.click();a.remove()}catch(e){}MELD("Klaar: "+Object.keys(out).length+" artiesten tot en met "+end+(fouten.length?". Fouten: "+fouten.join(", "):"")+". Staat er nu een gc.json in Downloads (sta de download toe als Safari erom vraagt)? Upload die dan op github.com in de map data/incoming (Add file > Upload files, dezelfde bestandsnaam gc.json is prima)"+(kopie?". Anders staat de JSON op je klembord (Cmd+V)":""))})();
```

## Wat gebeurt er na de upload?

De workflow `.github/workflows/process_gc_upload.yml` controleert het bestand
(`scripts/process_gc_upload.py`), vult `data/analysis_data.json` aan met alleen de
ontbrekende dagen (bestaande dagen worden nooit overschreven), commit het resultaat en
verwijdert `gc.json` weer uit deze map.
