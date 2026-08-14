/** ISO 3166-1 alpha-2 → название (для выбора в форме). */
export const COUNTRIES: { code: string; name: string }[] = [
  { code: "AU", name: "Австралия" },
  { code: "AT", name: "Австрия" },
  { code: "AZ", name: "Азербайджан" },
  { code: "AL", name: "Албания" },
  { code: "AR", name: "Аргентина" },
  { code: "AM", name: "Армения" },
  { code: "BY", name: "Беларусь" },
  { code: "BE", name: "Бельгия" },
  { code: "BG", name: "Болгария" },
  { code: "BR", name: "Бразилия" },
  { code: "GB", name: "Великобритания" },
  { code: "HU", name: "Венгрия" },
  { code: "VN", name: "Вьетнам" },
  { code: "DE", name: "Германия" },
  { code: "HK", name: "Гонконг" },
  { code: "GR", name: "Греция" },
  { code: "GE", name: "Грузия" },
  { code: "DK", name: "Дания" },
  { code: "EG", name: "Египет" },
  { code: "IL", name: "Израиль" },
  { code: "IN", name: "Индия" },
  { code: "ID", name: "Индонезия" },
  { code: "IE", name: "Ирландия" },
  { code: "IS", name: "Исландия" },
  { code: "ES", name: "Испания" },
  { code: "IT", name: "Италия" },
  { code: "KZ", name: "Казахстан" },
  { code: "CA", name: "Канада" },
  { code: "CY", name: "Кипр" },
  { code: "KG", name: "Киргизия" },
  { code: "CN", name: "Китай" },
  { code: "KR", name: "Корея (Республика)" },
  { code: "LV", name: "Латвия" },
  { code: "LT", name: "Литва" },
  { code: "LU", name: "Люксембург" },
  { code: "MY", name: "Малайзия" },
  { code: "MT", name: "Мальта" },
  { code: "MX", name: "Мексика" },
  { code: "MD", name: "Молдова" },
  { code: "NL", name: "Нидерланды" },
  { code: "NZ", name: "Новая Зеландия" },
  { code: "NO", name: "Норвегия" },
  { code: "AE", name: "ОАЭ" },
  { code: "PL", name: "Польша" },
  { code: "PT", name: "Португалия" },
  { code: "RO", name: "Румыния" },
  { code: "RU", name: "Россия" },
  { code: "SA", name: "Саудовская Аравия" },
  { code: "RS", name: "Сербия" },
  { code: "SG", name: "Сингапур" },
  { code: "SK", name: "Словакия" },
  { code: "SI", name: "Словения" },
  { code: "US", name: "США" },
  { code: "TJ", name: "Таджикистан" },
  { code: "TH", name: "Таиланд" },
  { code: "TW", name: "Тайвань" },
  { code: "TR", name: "Турция" },
  { code: "UZ", name: "Узбекистан" },
  { code: "UA", name: "Украина" },
  { code: "FI", name: "Финляндия" },
  { code: "FR", name: "Франция" },
  { code: "HR", name: "Хорватия" },
  { code: "CZ", name: "Чехия" },
  { code: "CH", name: "Швейцария" },
  { code: "SE", name: "Швеция" },
  { code: "EE", name: "Эстония" },
  { code: "ZA", name: "ЮАР" },
  { code: "JP", name: "Япония" },
].sort((a, b) => a.name.localeCompare(b.name, "ru"));

const byCode = new Map(COUNTRIES.map((c) => [c.code, c.name]));

/** ISO2 → emoji-флаг (🇩🇪). */
export function countryFlag(code: string | null | undefined): string {
  const c = (code ?? "").trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(c)) return "";
  return String.fromCodePoint(...[...c].map((ch) => 0x1f1e6 - 65 + ch.charCodeAt(0)));
}

export function countryName(code: string | null | undefined): string {
  const c = (code ?? "").trim().toUpperCase();
  return byCode.get(c) ?? c;
}

export function countryLabel(code: string | null | undefined): string {
  const c = (code ?? "").trim().toUpperCase();
  if (!c) return "";
  const flag = countryFlag(c);
  const name = countryName(c);
  return flag ? `${flag} ${name}` : name;
}
