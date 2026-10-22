#!/usr/bin/env node
// Generates android/app/src/main/assets/team-brand-colors.json from the curated table below and refuses to write
// anything that does not name an existing identity in team-identities.json (league + sport + exact name).
// Colors are curated from the clubs' widely published primary/secondary brand colors; they are NOT machine-verified
// against official style guides. Teams absent from this table intentionally resolve to the neutral hero fallback.
import fs from "node:fs";
import zlib from "node:zlib";
const assets = new URL("../android/app/src/main/assets/", import.meta.url);
// Crest images are not part of the public tree; the maintainer's tree keeps them in src/private/assets-art (see docs/LICENSING-AUDIT.md).
const art = new URL("../android/app/src/private/assets-art/", import.meta.url);
const crestUrl = (file) => [art, assets].map((base) => new URL(file, base)).find((u) => fs.existsSync(u));
const ids = JSON.parse(fs.readFileSync(new URL("team-identities.json", assets), "utf8"));
const TABLE = `
NFL|football
Arizona Cardinals 97233F 000000;Atlanta Falcons A71930 000000;Baltimore Ravens 241773 000000;Buffalo Bills 00338D C60C30;Carolina Panthers 0085CA 101820;Chicago Bears 0B162A C83803;Cincinnati Bengals FB4F14 000000;Cleveland Browns 311D00 FF3C00;Dallas Cowboys 003594 869397;Denver Broncos FB4F14 002244;Detroit Lions 0076B6 B0B7BC;Green Bay Packers 203731 FFB612;Houston Texans 03202F A71930;Indianapolis Colts 002C5F A2AAAD;Jacksonville Jaguars 006778 D7A22A;Kansas City Chiefs E31837 FFB81C;Las Vegas Raiders 000000 A5ACAF;Los Angeles Chargers 0080C6 FFC20E;Los Angeles Rams 003594 FFA300;Miami Dolphins 008E97 FC4C02;Minnesota Vikings 4F2683 FFC62F;New England Patriots 002244 C60C30;New Orleans Saints D3BC8D 101820;New York Giants 0B2265 A71930;New York Jets 125740 000000;Philadelphia Eagles 004C54 A5ACAF;Pittsburgh Steelers FFB612 101820;San Francisco 49ers AA0000 B3995D;Seattle Seahawks 002244 69BE28;Tampa Bay Buccaneers D50A0A FF7900;Tennessee Titans 0C2340 4B92DB;Washington Commanders 5A1414 FFB612
MLB|baseball
Arizona Diamondbacks A71930 E3D4AD;Athletics 003831 EFB21E;Atlanta Braves CE1141 13274F;Baltimore Orioles DF4601 000000;Boston Red Sox BD3039 0C2340;Chicago Cubs 0E3386 CC3433;Chicago White Sox 27251F C4CED4;Cincinnati Reds C6011F 000000;Cleveland Guardians 00385D E50022;Colorado Rockies 33006F C4CED4;Detroit Tigers 0C2340 FA4616;Houston Astros EB6E1F 002D62;Kansas City Royals 004687 BD9B60;Los Angeles Angels BA0021 003263;Los Angeles Dodgers 005A9C EF3E42;Miami Marlins 00A3E0 EF3340;Milwaukee Brewers 12284B FFC52F;Minnesota Twins 002B5C D31145;New York Mets 002D72 FF5910;New York Yankees 0C2340 C4CED3;Philadelphia Phillies E81828 002D72;Pittsburgh Pirates FDB827 27251F;San Diego Padres 2F241D FFC425;San Francisco Giants FD5A1E 27251F;Seattle Mariners 0C2C56 005C5C;St. Louis Cardinals C41E3A 0C2340;Tampa Bay Rays 092C5C 8FBCE6;Texas Rangers 003278 C0111F;Toronto Blue Jays 134A8E 1D2D5C;Washington Nationals AB0003 14225A
Premier League|soccer
AFC Bournemouth DA291C 000000;Arsenal EF0107 063672;Aston Villa 670E36 95BFE5;Brentford E30613 FFFFFF;Brighton & Hove Albion 0057B8 FFFFFF;Chelsea 034694 DBA111;Coventry City 5CB8E4 000000;Crystal Palace 1B458F C4122E;Everton 003399 FFFFFF;Fulham 000000 CC0000;Hull City F5A12D 000000;Ipswich Town 0044AA FFFFFF;Leeds United 1D428A FFCD00;Liverpool C8102E 00B2A9;Manchester City 6CABDD 1C2C5B;Manchester United DA291C FBE122;Newcastle United 241F20 FFFFFF;Nottingham Forest DD0000 FFFFFF;Sunderland EB172B FFFFFF;Tottenham Hotspur 132257 FFFFFF
La Liga|soccer
Alavés 0761AF FFFFFF;Athletic Club EE2523 FFFFFF;Atlético Madrid CB3524 272E61;Barcelona A50044 004D98;Celta Vigo 8AC3EE FFFFFF;Deportivo 0A4C9A FFFFFF;Elche 007A3D FFFFFF;Espanyol 007FC8 FFFFFF;Getafe 005999 FFFFFF;Osasuna D91A21 0A1C3E;Rayo Vallecano E30613 FFFFFF;Real Betis 00954C FFFFFF;Real Madrid 00529F FEBE10;Real Sociedad 0067B1 FFFFFF;Sevilla D4021D FFFFFF;Valencia EE7203 000000;Villarreal FBE10F 005187
Serie A|soccer
AC Milan FB090B 000000;AS Roma 8E1F2F F0BC42;Atalanta 1E71B8 000000;Bologna 1A2F48 A21C26;Cagliari A00E32 002B5C;Como 0055A5 FFFFFF;Fiorentina 4B2E83 FFFFFF;Frosinone FFDD00 005BAC;Genoa AA0000 00205B;Internazionale 0068A8 000000;Juventus 000000 FFFFFF;Lazio 87D8F7 FFFFFF;Lecce FFD100 E4002B;Monza E4002B FFFFFF;Napoli 12A0D7 FFFFFF;Parma FFD100 00205B;Sassuolo 00A650 000000;Torino 8B1A1A FFFFFF;Udinese 000000 FFFFFF;Venezia 00693E F58220
Bundesliga|soccer
1. FC Union Berlin EB1923 FFFFFF;Bayer Leverkusen E32221 000000;Bayern Munich DC052D 0066B2;Borussia Dortmund FDE100 000000;Borussia Mönchengladbach 00A650 000000;Eintracht Frankfurt E1000F 000000;FC Augsburg BA3733 FFFFFF;FC Cologne ED1C24 FFFFFF;Hamburg SV 005CA9 FFFFFF;Mainz C3141E FFFFFF;RB Leipzig DD0741 1E2333;SC Freiburg E2001A 000000;SC Paderborn 07 004C97 000000;Schalke 04 004D9D FFFFFF;TSG Hoffenheim 1961B5 FFFFFF;VfB Stuttgart E32219 FFFFFF;Werder Bremen 1D9053 FFFFFF
Ligue 1|soccer
AJ Auxerre 003D8F FFFFFF;AS Monaco E3001B FFFFFF;Angers 000000 FFFFFF;Brest E2001A FFFFFF;Le Havre AC 00A0DD 00205B;Lens E4002B FFD100;Lille E01E13 1E2A5A;Lorient FF7F00 000000;Lyon 0C3B8E DA0812;Marseille 009EDB FFFFFF;Nice E31B23 000000;Paris Saint-Germain 004170 DA291C;Stade Rennais E13327 000000;Strasbourg 009FE3 FFFFFF;Toulouse 6F2C91 FFFFFF;Troyes 0057A8 FFFFFF
NCAA|football
Air Force Falcons 003087 8A8D8F;Alabama Crimson Tide 9E1B32 FFFFFF;Arizona State Sun Devils 8C1D40 FFC627;Arizona Wildcats CC0033 003366;Arkansas Razorbacks 9D2235 000000;Army Black Knights 000000 D4BF91;Auburn Tigers 0C2340 E87722;BYU Cougars 002E5D FFFFFF;Baylor Bears 154734 FFB81C;Boise State Broncos 0033A0 D64309;Boston College Eagles 8A100B B29D6C;California Golden Bears 003262 FDB515;Cincinnati Bearcats E00122 000000;Clemson Tigers F56600 522D80;Colorado Buffaloes CFB87C 000000;Duke Blue Devils 003087 FFFFFF;Florida Gators 0021A5 FA4616;Florida State Seminoles 782F40 CEB888;Fresno State Bulldogs DB0032 002E5D;Georgia Bulldogs BA0C2F 000000;Georgia Tech Yellow Jackets B3A369 003057;Houston Cougars C8102E FFFFFF;Illinois Fighting Illini E84A27 13294B;Indiana Hoosiers 990000 FFFFFF;Iowa Hawkeyes FFCD00 000000;Iowa State Cyclones C8102E F1BE48;Kansas Jayhawks 0051BA E8000D;Kansas State Wildcats 512888 FFFFFF;Kentucky Wildcats 0033A0 FFFFFF;LSU Tigers 461D7C FDD023;Liberty Flames 002D62 C8102E;Louisville Cardinals AD0000 000000;Maryland Terrapins E03A3E FFD520;Memphis Tigers 003087 898D8D;Miami Hurricanes F47321 005030;Michigan State Spartans 18453B FFFFFF;Michigan Wolverines 00274C FFCB05;Minnesota Golden Gophers 7A0019 FFCC33;Mississippi State Bulldogs 5D1725 FFFFFF;Missouri Tigers F1B82D 000000;NC State Wolfpack CC0000 000000;Navy Midshipmen 00205B C5B783;Nebraska Cornhuskers E41C38 FFFFFF;North Carolina Tar Heels 7BAFD4 13294B;Northern Illinois Huskies BA0C2F 000000;Northwestern Wildcats 4E2A84 FFFFFF;Notre Dame Fighting Irish 0C2340 C99700;Ohio State Buckeyes BB0000 666666;Oklahoma Sooners 841617 FDF9D8;Oklahoma State Cowboys FF7300 000000;Ole Miss Rebels CE1126 14213D;Oregon Ducks 154733 FEE123;Oregon State Beavers DC4405 000000;Penn State Nittany Lions 041E42 FFFFFF;Pittsburgh Panthers 003594 FFB81C;Purdue Boilermakers 000000 CEB888;Rutgers Scarlet Knights CC0033 5F6A72;SMU Mustangs C8102E 354CA1;San Diego State Aztecs A6192E 000000;South Carolina Gamecocks 73000A 000000;Stanford Cardinal 8C1515 FFFFFF;Syracuse Orange F76900 000E54;TCU Horned Frogs 4D1979 A3A9AC;Tennessee Volunteers FF8200 58595B;Texas A&M Aggies 500000 FFFFFF;Texas Longhorns BF5700 FFFFFF;Texas Tech Red Raiders CC0000 000000;Toledo Rockets 15397F FFD200;Tulane Green Wave 006747 418FDE;UCF Knights BA9B37 000000;UCLA Bruins 2D68C4 F2A900;USC Trojans 990000 FFC72C;Utah State Aggies 0F2439 FFFFFF;Utah Utes CC0000 FFFFFF;Vanderbilt Commodores 866D4B 000000;Virginia Cavaliers 232D4B F84C1E;Virginia Tech Hokies 861F41 E5751F;Wake Forest Demon Deacons 9E7E38 000000;Washington Huskies 4B2E83 B7A57A;Washington State Cougars 981E32 5E6A71;West Virginia Mountaineers 002855 EAAA00;Wisconsin Badgers C5050C FFFFFF
`;
const teams = [];
let league = "", sport = "";
for (const line of TABLE.split("\n").map(l => l.trim()).filter(Boolean)) {
  if (line.includes("|")) { [league, sport] = line.split("|"); continue; }
  for (const entry of line.split(";")) {
    const m = entry.trim().match(/^(.*) ([0-9A-F]{6}) ([0-9A-F]{6})$/);
    if (!m) throw new Error(`Bad entry: ${entry}`);
    const [, name, color, secondary] = m;
    if (!ids.some(i => i.league === league && i.sport === sport && i.name === name)) throw new Error(`No such identity: ${league}/${sport}/${name}`);
    teams.push({ league, sport, name, color, secondary });
  }
}
const curated = teams.length;
const keys0 = new Set(teams.map(t => `${t.league}|${t.sport}|${t.name}`));

// Identities without a curated entry get colors derived from their own bundled crest (dominant saturated hue buckets),
// so branding follows the real badge instead of a guess. Monochrome or undecodable crests stay uncolored (neutral hero).
function decodePng(file) {
  const b = fs.readFileSync(file);
  let o = 8, w, h, bd, ct, il; const idat = []; let plte = null, trns = null;
  while (o < b.length) {
    const l = b.readUInt32BE(o), t = b.toString("ascii", o + 4, o + 8), d = b.subarray(o + 8, o + 8 + l);
    if (t === "IHDR") { w = d.readUInt32BE(0); h = d.readUInt32BE(4); bd = d[8]; ct = d[9]; il = d[12]; }
    else if (t === "PLTE") plte = d; else if (t === "tRNS") trns = d; else if (t === "IDAT") idat.push(d);
    o += 12 + l;
  }
  if (bd !== 8 || il !== 0) return null;
  const ch = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[ct]; if (!ch) return null;
  const raw = zlib.inflateSync(Buffer.concat(idat)), st = w * ch, px = Buffer.alloc(st * h);
  for (let y = 0; y < h; y++) {
    const ft = raw[y * (st + 1)];
    for (let x = 0; x < st; x++) {
      const r = raw[y * (st + 1) + 1 + x], a = x >= ch ? px[y * st + x - ch] : 0, u = y ? px[(y - 1) * st + x] : 0, c = x >= ch && y ? px[(y - 1) * st + x - ch] : 0;
      let v; switch (ft) { case 0: v = r; break; case 1: v = r + a; break; case 2: v = r + u; break; case 3: v = r + ((a + u) >> 1); break;
        default: { const q = a + u - c, pa = Math.abs(q - a), pb = Math.abs(q - u), pc = Math.abs(q - c); v = r + (pa <= pb && pa <= pc ? a : pb <= pc ? u : c); } }
      px[y * st + x] = v & 255;
    }
  }
  const rgba = (x, y) => { const i = y * st + x * ch;
    if (ct === 6) return [px[i], px[i + 1], px[i + 2], px[i + 3]];
    if (ct === 2) return [px[i], px[i + 1], px[i + 2], 255];
    if (ct === 4) return [px[i], px[i], px[i], px[i + 1]];
    if (ct === 0) return [px[i], px[i], px[i], 255];
    const k = px[i]; return [plte[k * 3], plte[k * 3 + 1], plte[k * 3 + 2], trns && k < trns.length ? trns[k] : 255]; };
  return { w, h, rgba };
}
function crestColors(file) {
  const img = decodePng(file); if (!img) return null;
  const buckets = new Map();
  for (let y = 0; y < img.h; y += 2) for (let x = 0; x < img.w; x += 2) {
    const [r, g, bl, a] = img.rgba(x, y); if (a < 200) continue;
    const mx = Math.max(r, g, bl), mn = Math.min(r, g, bl), sat = mx ? (mx - mn) / mx : 0;
    if (sat < 0.4 || mx < 70) continue;
    const k = (r >> 5) << 6 | (g >> 5) << 3 | (bl >> 5), e = buckets.get(k) ?? { n: 0, r: 0, g: 0, b: 0 };
    e.n++; e.r += r; e.g += g; e.b += bl; buckets.set(k, e);
  }
  const list = [...buckets.values()].sort((p, q) => q.n - p.n).map(e => [Math.round(e.r / e.n), Math.round(e.g / e.n), Math.round(e.b / e.n), e.n]);
  if (!list.length || list[0][3] < 40) return null;
  const hex = c => c.slice(0, 3).map(v => v.toString(16).padStart(2, "0")).join("").toUpperCase();
  const far = list.slice(1).find(c => Math.hypot(c[0] - list[0][0], c[1] - list[0][1], c[2] - list[0][2]) > 90 && c[3] >= list[0][3] * 0.15);
  return { color: hex(list[0]), secondary: far ? hex(far) : "FFFFFF" };
}
for (const i of ids) {
  if (keys0.has(`${i.league}|${i.sport}|${i.name}`)) continue;
  const c = i.file && crestUrl(i.file) ? crestColors(crestUrl(i.file)) : null;
  if (c) teams.push({ league: i.league, sport: i.sport, name: i.name, color: c.color, secondary: c.secondary, origin: "crest" });
}
const keys = new Set(teams.map(t => `${t.league}|${t.sport}|${t.name}`));
if (keys.size !== teams.length) throw new Error("Duplicate identity in table");
fs.writeFileSync(new URL("team-brand-colors.json", assets), JSON.stringify({ version: 1, source: "curated-brand-colors-not-machine-verified", teams }) + "\n");
console.log(`wrote ${teams.length} color entries (${curated} curated, ${teams.length - curated} crest-derived)`);
