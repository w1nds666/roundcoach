/** 地图自然名（显示用）；未知返回原 map_name。 */
const MAP_NAMES = {
  de_mirage: 'Mirage', de_anubis: 'Anubis', de_dust2: 'Dust II', de_inferno: 'Inferno',
  de_ancient: 'Ancient', de_nuke: 'Nuke', de_overpass: 'Overpass', de_train: 'Train',
  de_vertigo: 'Vertigo', de_cache: 'Cache', de_cobblestone: 'Cobblestone', de_italy: 'Italy',
  de_basalt: 'Basalt', de_brewery: 'Brewery', de_dogtown: 'Dogtown', de_edin: 'Edin',
  de_grail: 'Grail', de_jura: 'Jura', de_palais: 'Palais', de_whistle: 'Whistle',
}

export function mapLabel(mapName) {
  if (!mapName) return ''
  return MAP_NAMES[mapName] || mapName
}
