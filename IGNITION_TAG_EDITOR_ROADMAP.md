# Ignition Tag Editor – razvojna smer in implementacijski načrt

**Status dokumenta:** delovna produktna usmeritev  
**Ciljno okolje:** Calcit, Ignition 8.3  
**Primarni lokaciji:** Stahovica in Gospić  
**Datum:** 23. julij 2026

## 1. Namen programa

Cilj je izdelati namizni program, ki zna iz obstoječih Ignition izvozov:

1. prebrati in ohraniti celotno strukturo IO, UNS in UDT tagov;
2. povezati surove IO tage, urejene IO tage, UDT člane in UNS instance;
3. primerjati trenutno stanje s pravili in referenčnimi Excel tabelami;
4. predlagati preimenovanja, premike, nove tage in spremembe referenc;
5. uporabniku omogočiti pregled ter potrditev vsake spremembe;
6. pred izvozom simulirati in validirati končno stanje;
7. izdelati varen Ignition JSON za celoten obseg ali samo za izbrane tage;
8. po ponovnem izvozu iz Ignitiona preveriti, ali je nameščeno stanje enako načrtovanemu.

Program ni neposredni urejevalnik aktivnega Gatewaya. Izvorni JSON ostane nespremenjen, vse spremembe se najprej vodijo v ločenem delovnem modelu, v Ignition pa se prenesejo šele prek pregledanega izvoza.

## 2. Osnovne produktne odločitve

### 2.1 Calcit-first

Prva različica je namenjena resničnim podatkom Calcita in mora zanesljivo podpirati Stahovico ter Gospić. Pravila naj bodo zapisana konfiguracijsko, da jih bo pozneje mogoče razširiti, vendar prva različica ni generičen urejevalnik za vsak Ignition projekt.

### 2.2 Izvorni podatki so nespremenljivi

Datoteke v `data/raw` so vhod in se nikoli ne prepisujejo. Program hrani:

- originalni objekt;
- normalizirani notranji model;
- ločene načrtovane spremembe;
- simulirano končno stanje;
- izvožene datoteke in poročila.

### 2.3 Povezave morajo biti razložljive

Program ne sme samo trditi, da sta taga povezana. Za vsako povezavo mora prikazati:

- uporabljen dokaz;
- izvor pravila;
- stopnjo zanesljivosti;
- morebitne konflikte;
- razlog, če povezave ni bilo mogoče določiti.

### 2.4 Program predlaga, uporabnik potrdi

Samodejno zaznana sprememba še ni odobrena sprememba. Uporabnik mora imeti možnost predlog:

- potrditi;
- zavrniti;
- ročno popraviti;
- začasno pustiti nerešen;
- razveljaviti.

### 2.5 Najmanjši varen izvoz je privzet

Program podpira dva načina:

- **polni izvoz** za varnostno kopijo, celotno migracijo ali obnovo;
- **omejeni izvoz** za običajno delo, kjer se izvozi samo izbran tag, več izbranih tagov, mapa, UDT instanca ali logični sklop.

Običajni delovni tok uporablja omejeni izvoz.

## 3. Trenutno stanje

Analitična osnova je že izdelana:

- uvoz IO, UNS in UDT JSON;
- indeksiranje približno 277.000 vozlišč v SQLite;
- iskanje in pregled strukture;
- razreševanje UDT definicij, dedovanja in parametrov;
- validator z razlago ugotovitev;
- poročila v Markdown, CSV in JSON;
- testi za glavne posebnosti Ignition podatkov;
- ohranjanje izvornih datotek brez sprememb.

To pomeni, da program podatke že zna prebrati in analizirati. Naslednji cilj ni izvoz ali končni uporabniški vmesnik, temveč zanesljiv model pričakovanega stanja in mapping med vsemi plastmi tagov.

## 4. Ciljni delovni tok

```text
Ustvari ali odpri delovni projekt
→ uvozi IO, UNS, UDT in referenčne tabele
→ izberi lokacijo, linijo ali vejo
→ preglej zaznane naprave in sklope
→ preglej povezave med vsemi plastmi tagov
→ razreši nejasnosti
→ ustvari predloge sprememb
→ potrdi ali popravi predloge
→ simuliraj končno stanje
→ zaženi validacijo in preglej diff
→ dodaj želene tage v izvozno košarico ali izberi polni izvoz
→ ustvari Ignition JSON in poročilo
→ uvozi JSON v Ignition
→ ponovno izvozi isti obseg iz Ignitiona
→ preveri enakost nameščenega in načrtovanega stanja
```

## 5. Faze implementacije

### Faza 0 – analitična osnova

**Status:** zaključeno

Obsega parser, SQLite indeks, UDT resolver, validator, poročila in obstoječe teste.

Preostale anomalije se dokumentirajo in obravnavajo ločeno. Posamezna znana anomalija ne sme ustaviti razvoja celotnega produkta, če ne vpliva na naslednjo fazo.

### Faza 1 – referenčni podatki in pričakovano stanje

**Status:** naslednji razvojni korak

Program mora uvoziti:

- referenčne Excel tabele, kot sta `L400.xlsx` in `L1600.xlsx`;
- legendo sklopov;
- primere pravilnega končnega poimenovanja;
- pozneje ročno potrjene mapping tabele.

Podatki iz različnih tabel se pretvorijo v enoten notranji model. Najmanjši pričakovani nabor polj je:

- lokacija;
- proizvodnja;
- linija;
- tip naprave ali sklopa;
- tehnološka številka;
- izvorno ime;
- pričakovano ciljno ime;
- član sklopa;
- pričakovani UDT tip;
- obveznost člana;
- opomba;
- izvorna datoteka, list in vrstica;
- stanje veljavnosti reference.

Program mora zaznati:

- manjkajoča obvezna polja;
- podvojene reference;
- nasprotujoča si pravila;
- neznane tipe sklopov;
- vrstice, ki jih ni mogoče normalizirati;
- razlike med lokacijami.

**Rezultat faze:** program lahko za izbran sklop odgovori, kakšno stanje je pričakovano in iz katere reference ta trditev izhaja.

### Faza 2 – relacijski oziroma mapping pogon

Mapping pogon poveže:

```text
surovi IO tag
→ obstoječi urejeni IO tag
→ pričakovani urejeni IO tag
→ UDT član
→ UNS UDT instanca
```

Uporabljeni dokazi lahko vključujejo:

- enak `opcItemPath`;
- `sourceTagPath`;
- UDT definicijo;
- UDT parametre;
- tehnološko številko;
- obstoječo hierarhijo;
- pravilo sklopa;
- referenčno Excel vrstico;
- ročno potrjeno povezavo.

Stanje povezave ni samo `true` ali `false`, temveč eno od:

- `EXACT`;
- `INFERRED`;
- `AMBIGUOUS`;
- `MISSING`;
- `CONFLICT`;
- `EXTERNAL`;
- `NOT_APPLICABLE`.

Ročno potrjena povezava ima prednost pred prihodnjim samodejnim ugibanjem, vendar mora ostati sledljivo, kdo oziroma katero pravilo jo je potrdilo.

**Rezultat faze:** za izbran tag ali sklop je mogoče prikazati celotno verigo, dokaze in nerešene vrzeli.

### Faza 3 – formalna pravila sklopov

Na podlagi resničnih primerov se formalizirajo tipi, kot so:

- meritve;
- motorji;
- ventili;
- regulatorji;
- stikala;
- filtri;
- izpihe in podpih;
- konusi;
- linijske tipke;
- custom sklopi.

Za vsak tip se določi:

- način prepoznave;
- pričakovani člani;
- obvezni in izbirni člani;
- pravilo ciljnega imena;
- ciljna mapa;
- UDT tip;
- parametri;
- dovoljene izjeme;
- preverjanja popolnosti.

Pravila morajo biti v konfiguraciji ali ločenem domenskem modelu, ne razpršena po poljubnih delih Python kode.

**Rezultat faze:** program zna za zaznan sklop izračunati njegovo pričakovano strukturo.

### Faza 4 – prvi navpični rez na eni liniji

Izbere se ena referenčna linija, na primer L400 ali L1600, in se zanjo dokonča celoten read-only tok:

1. uvoz referenc;
2. zaznava sklopov;
3. povezava surovih in urejenih IO tagov;
4. izračun pričakovanih imen;
5. povezava UDT članov in UNS instanc;
6. prikaz razlik;
7. poročilo;
8. primerjava z ročno potrjenim rezultatom.

Ta linija postane **golden dataset**. Njeni pričakovani rezultati se shranijo v testne fixture, da poznejše spremembe ne pokvarijo že potrjenega vedenja.

**Rezultat faze:** uporaben read-only CLI-pogon za eno resnično linijo.

### Faza 5 – read-only uporabniški explorer

Prvi vizualni program mora omogočati:

- odpiranje delovnega projekta;
- izbiro lokacije, providerja, linije ali veje;
- drevesni pregled IO, UNS in UDT;
- hitro iskanje in filtre;
- podrobnosti izbranega taga;
- prikaz povezovalne verige;
- primerjavo trenutnega in pričakovanega stanja;
- pregled referenčne Excel vrstice;
- validator in skok iz ugotovitve na tag;
- prikaz stanja sklopa, na primer `COMPLETE`, `INCOMPLETE`, `AMBIGUOUS` ali `CONFLICT`.

V tej fazi program ne spreminja podatkov.

**Rezultat faze:** prvi program, ki je uporaben za vsakodnevno razumevanje in preverjanje velike Ignition strukture.

### Faza 6 – model načrtovanih sprememb

Spremembe se ne zapisujejo neposredno v uvožena vozlišča. Vsaka sprememba je ločena operacija:

- `CREATE_TAG`;
- `RENAME_TAG`;
- `MOVE_TAG`;
- `UPDATE_PROPERTY`;
- `UPDATE_SOURCE_PATH`;
- `UPDATE_PARAMETERS`;
- `DELETE_TAG`.

Operacija vsebuje:

- identiteto ciljnega taga;
- staro stanje;
- novo stanje;
- razlog;
- uporabljeno pravilo;
- stopnjo zanesljivosti;
- povezane tage;
- posledice za reference;
- status odobritve;
- čas spremembe.

Program mora omogočiti razveljavitev in ponovno izvedbo, ne da bi spremenil izvorni uvoz.

**Rezultat faze:** varen delovni model, v katerem je mogoče načrtovati spremembe.

### Faza 7 – predlogi preimenovanja in strukturiranja

Program za izbrani obseg predlaga:

- nova imena;
- ciljne mape;
- nove urejene IO tage;
- nove UNS instance;
- pripadajoče UDT tipe;
- potrebne parametre;
- spremembe `sourceTagPath`;
- manjkajoče člane;
- custom sklope.

Nejasen primer ne sme biti samodejno potrjen. Ostane v stanju `AMBIGUOUS` ali `CONFLICT` in zahteva odločitev uporabnika.

**Rezultat faze:** uporabnik lahko pripravi in potrdi celoten načrt preureditve izbrane linije ali veje.

### Faza 8 – simulacija, diff in validacija končnega stanja

Program vse potrjene operacije uporabi na kopiji notranjega modela in prikaže:

- ustvarjene tage;
- preimenovane in premaknjene tage;
- spremenjene lastnosti;
- spremenjene reference;
- odstranjene tage;
- nerešene elemente;
- podvojene ciljne poti;
- manjkajoče zahtevane člane;
- neveljavne UDT tipe ali parametre.

Validator se požene nad simuliranim končnim stanjem. Izvoz je blokiran, če obstaja kritična napaka, ki lahko ustvari izgubo podatkov ali neveljavno Ignition strukturo.

**Rezultat faze:** dokazljivo veljavno načrtovano stanje pred izdelavo JSON.

### Faza 9 – izvozni sistem

Izvoz pride šele po stabilnem notranjem modelu sprememb in validaciji. Podpira polni ter omejeni izvoz.

#### 9.1 Polni izvoz

Polni izvoz vsebuje celoten izbran provider oziroma celoten obseg projekta. Uporablja se za:

- varnostno kopijo;
- večjo migracijo;
- postavitev praznega testnega providerja;
- primerjavo celotnega stanja;
- obnovo.

Polni izvoz ni privzet način uvajanja manjših dnevnih sprememb.

#### 9.2 Izvozna košarica

Uporabnik lahko med pregledovanjem dodaja elemente v **izvozno košarico** in šele pozneje izdela skupni izvoz.

V košarico je mogoče dodati:

- posamezen navaden tag;
- več tagov iz iste ali različnih map;
- mapo ali njeno vsebino;
- celotno UDT instanco;
- logični sklop;
- vse potrjene spremembe izbrane linije;
- kombinacijo elementov iz več providerjev, na primer `IO_GOS_SIE` in `UNS_GOS`.

Košarica mora omogočati:

- dodajanje in odstranjevanje elementov;
- prikaz providerja in polne poti;
- prikaz, ali je element spremenjen ali samo dodan zaradi odvisnosti;
- samodejno odstranjevanje podvojenih izbir;
- združevanje prekrivajočih se izborov;
- opozorilo, če izbira vključuje celotno mapo namesto samo otrok;
- prikaz pričakovanega obsega uvoza;
- shranitev izbora z delovnim projektom;
- čiščenje košarice po uspešno potrjenem izvozu.

Primer: če uporabnik izbere tri IO tage in dve UNS instanci, program izdela ločene neposredno uvozljive JSON datoteke glede na provider in ciljno mapo, vendar jih združi v en izvozni paket z enim poročilom.

#### 9.3 Samodejna razširitev obsega

Program mora preprečiti tehnično nevarne delne izbire:

- podedovanega člana UDT instance se ne izvozi kot samostojen tag;
- izbor člana se razširi na lastniško UDT instanco;
- UDT definicija se ne doda samodejno brez opozorila;
- če ciljna instanca zahteva definicijo, ki v ciljnem sistemu morda ne obstaja, se doda odvisnost oziroma blokada;
- sprememba reference lahko v košarico predlaga tudi povezani tag, vendar ga brez uporabnikove potrditve ne sme tiho dodati kot spremembo.

#### 9.4 Izvozni paket

En omejeni izvoz lahko vsebuje:

```text
export_2026-07-23_L1600/
├── manifest.json
├── IO_GOS_SIE/
│   ├── target_01/
│   │   └── tags.json
│   └── target_02/
│       └── tags.json
├── UNS_GOS/
│   └── target_01/
│       └── instances.json
└── import_report.md
```

Ignition JSON datoteke ostanejo čiste in neposredno uvozljive. Podatki, ki so namenjeni samo našemu programu, se zapišejo v `manifest.json`.

Manifest za vsako datoteko vsebuje najmanj:

- provider;
- vrsto obsega;
- izvorne polne poti;
- zahtevani `importBasePath`;
- korenske elemente JSON datoteke;
- uporabljeno collision policy;
- zahtevane UDT tipe;
- prizadete poti;
- baseline hash;
- hash izvoza;
- opozorila in vrstni red uvoza.

`import_report.md` uporabniku poda točen vrstni red in ciljno mapo za vsak korak uvoza.

#### 9.5 Ohranjanje polne konfiguracije

Generator začne iz originalnega konfiguracijskega objekta in nanj uporabi samo potrjene spremembe. Ohraniti mora:

- alarme;
- history nastavitve;
- bindings;
- tag event skripte;
- permissions;
- podatkovni tip;
- scaling;
- OPC nastavitve;
- `typeId`;
- parametre instance;
- lokalne UDT override;
- vse druge znane in neznane lastnosti.

Efektivna UDT definicija se ne sme razširiti v posamezno instanco, saj bi to ustvarilo nove lokalne override.

#### 9.6 Ciljna lokacija pri uvozu

Ignition JSON ne zagotavlja samostojne absolutne umestitve taga. Rezultat je odvisen od mape oziroma `basePath`, kamor se datoteka uvozi. Zato mora vsak omejeni izvoz:

- vsebovati točen `importBasePath`;
- ločiti tage, ki potrebujejo različne ciljne mape;
- prikazati korenske elemente, ki bodo uvoženi;
- preprečiti zavajajoč izvoz umetno ustvarjene celotne nadrejene strukture;
- opozoriti, da `Overwrite` popolnoma prepiše istoimenski tag.

V interaktivnem uvozu Ignition 8.1 sta za kolizije na voljo samo `Overwrite` in `Ignore`. Uvoz UDT definicij je ločen in strožje nadzorovan, ker lahko `Overwrite` odstrani člane, ki jih datoteka ne vsebuje.

#### 9.7 Preverjanje enakosti

Program uporablja štiri stopnje preverjanja:

1. **Baseline hash** – stanje ob začetnem uvozu.
2. **Lokalni round-trip** – ponovni uvoz generiranega JSON v lasten parser.
3. **Affected paths diff** – dokaz, katere poti lahko izvoz spremeni.
4. **Post-import verification** – ponovni izvoz istega obsega iz Ignitiona in primerjava s pričakovanim stanjem.

Možna statusa sta:

- `EXPORT_GENERATED` – datoteka je izdelana in lokalno preverjena;
- `DEPLOYED_AND_VERIFIED` – stanje je bilo po uvozu ponovno izvoženo iz Ignitiona in potrjeno.

**Rezultat faze:** varen polni ali omejeni JSON izvoz z jasnim obsegom, navodili, round-trip preverjanjem in možnostjo potrditve nameščenega stanja.

### Faza 10 – produktizacija

Zaključna faza vključuje:

- shranjevanje in ponovno odpiranje delovnih projektov;
- napredovanje pri dolgih operacijah;
- možnost preklica;
- uporabna sporočila napak;
- stabilno delovanje nad več sto tisoč vozlišči;
- različice podatkovne baze in migracije;
- varnostne kopije delovnega projekta;
- namestitveni paket za uporabo brez VS Code;
- navodila za uporabo;
- testni postopek za nove izdaje.

## 6. Mejniki

| Mejnik | Preverljiv rezultat |
|---|---|
| M1 | Referenčni podatki in read-only mapping za eno linijo |
| M2 | Uporaben vizualni explorer |
| M3 | Predlogi, potrjevanje in simulacija sprememb |
| M4 | Izvozna košarica, omejeni izvoz in polni izvoz |
| M5 | Round-trip in post-import preverjanje |
| M6 | Zapakiran program za redno uporabo |

## 7. Izrecno zunaj prve različice

Prva različica ne vključuje:

- neposrednega pisanja v aktivni produkcijski Gateway;
- neposrednega spreminjanja PLC naslovov;
- samodejnega odločanja pri dvoumnih povezavah;
- splošnega urejanja UDT definicij;
- večuporabniškega ali cloud delovanja;
- generične podpore vsem možnim Ignition projektom;
- tihega brisanja tagov;
- uvoza brez predhodnega diff-a in validacije.

## 8. Naslednji konkretni korak

Izvozni sistem je s tem dokumentom dovolj jasno določen in ga trenutno ni treba podrobneje načrtovati.

Naslednja implementacijska naloga je:

> Uvoz referenčnih Excel tabel in izdelava enotnega notranjega modela pričakovanega stanja, skupaj z validacijo vrstic, sledljivostjo izvora in podporo razlikam med lokacijami.

Po tej fazi sledi mapping pogon. Uporabniški vmesnik, urejanje in izvoz se začnejo šele, ko je mapping preverjen na eni resnični liniji.

## 9. Merilo končnega uspeha

Program je pripravljen za redno uporabo, ko lahko na resnični liniji:

1. pravilno rekonstruira vse relevantne relacije;
2. pokaže trenutno in pričakovano stanje;
3. razloži vsako samodejno odločitev;
4. pripravi pregledljive predloge sprememb;
5. po potrditvi simulira veljaven rezultat;
6. izvozi celoten provider ali samo vsebino izvozne košarice;
7. zagotovi, da JSON ohrani vse nepoznane in nespremenjene lastnosti;
8. dokaže natančen obseg vpliva;
9. po uvozu v Ignition potrdi enakost nameščenega in načrtovanega stanja.

## 10. Referenčna dokumentacija

- [Ignition 8.1 – Exporting and Importing Tags](https://www.docs.inductiveautomation.com/docs/8.1/platform/tags/exporting-and-importing-tags)
- [Ignition 8.1 – system.tag.importTags](https://www.docs.inductiveautomation.com/docs/8.1/appendix/scripting-functions/system-tag/system-tag-importTags)
