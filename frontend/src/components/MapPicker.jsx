import { CircleMarker, MapContainer, Rectangle, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import { useEffect } from 'react'

function Clicks({ onClick }) {
  useMapEvents({ click: e => onClick(e.latlng.lat, e.latlng.lng) })
  return null
}

function Fly({ target }) {
  const map = useMap()
  useEffect(() => {
    if (!target) return
    if (target.bounds) map.fitBounds(target.bounds, { maxZoom: 7 })
    else map.setView([target.lat, target.lon], Math.max(map.getZoom(), 6))
  }, [target, map])
  return null
}

export default function MapPicker({ mode, point, box, pending, onPick }) {
  const bounds = box ? [[box.south, box.west], [box.north, box.east]] : null
  const fly = box ? { bounds } : point
  return (
    <MapContainer center={[22, 79]} zoom={4} minZoom={2} worldCopyJump className="map" aria-label="Region map">
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {mode !== 'city' && <Clicks onClick={onPick} />}
      <Fly target={fly} />
      {point && !box && <CircleMarker center={[point.lat, point.lon]} radius={9} pathOptions={{ color: '#b42318', fillOpacity: 0.6 }} />}
      {pending && <CircleMarker center={[pending.lat, pending.lon]} radius={6} pathOptions={{ color: '#b42318' }} />}
      {bounds && <Rectangle bounds={bounds} pathOptions={{ color: '#b42318', weight: 2 }} />}
    </MapContainer>
  )
}
