import { useTranslation } from 'react-i18next'
import TabHero from '../../../components/TabHero.jsx'
import TripPlannerCard from '../../../components/TripPlannerCard.jsx'
import CrowdForecastCard from '../../../components/CrowdForecastCard.jsx'
import DiscoveryCard from '../../../components/DiscoveryCard.jsx'
import OfflineTripCard from '../../../components/OfflineTripCard.jsx'
import TwoWayVoiceTranslator from '../../../components/TwoWayVoiceTranslator.jsx'
import FairPriceCard from '../../../components/FairPriceCard.jsx'
import CulturalEtiquetteCard from '../../../components/CulturalEtiquetteCard.jsx'
import CurrencyCard from '../../../components/CurrencyCard.jsx'
import { Card } from '../../../components/ui.jsx'
import LocationStatus from '../../../components/LocationStatus.jsx'
import useCurrentLocation from '../../../hooks/useCurrentLocation.js'

// Explore: trip planning, crowd/queue forecasting, off-the-beaten-path
// discovery, the multilingual phrasebook, a two-way voice translator, the
// local festival calendar, and permit/e-pass automation -- everything about
// planning and enriching the trip itself, distinct from PlanTab's
// safety-route planning and HelpTab's emergency assistance.
export default function ExploreTab({ data }) {
  const { t, i18n } = useTranslation()
  const { me, tid } = data
  // The tourist's real position: their last tracked GPS fix, or one taken from the
  // device on request. Never a default map centre -- without a real position the
  // location-based cards ask for it instead of showing results for the wrong place.
  const loc = useCurrentLocation()
  const stored = me?.last_lat != null && me?.last_lng != null ? { lat: me.last_lat, lng: me.last_lng } : null
  const here = stored || loc.position
  const lat = here?.lat
  const lng = here?.lng
  const lang = i18n.resolvedLanguage || i18n.language

  return (
    <div className="space-y-4">
      <TabHero icon="🌍" title={t('explore_hero.title')} subtitle={t('explore_hero.subtitle')}
        gradient="from-emerald-500 via-teal-500 to-cyan-600" />

      <TripPlannerCard touristId={tid} />
      <CrowdForecastCard />
      {/* Discovery finds its own position (device GPS, or a typed place) -- see the card. */}
      <DiscoveryCard />
      {here ? (
        <>
          <OfflineTripCard touristId={tid} me={me} lat={lat} lng={lng} />
        </>
      ) : (
        <Card title={t('explore.near_you', 'Near you')} icon="📍" iconColor="bg-sky-50 text-sky-600 dark:bg-sky-900/30 dark:text-sky-300">
          <p className="text-xs text-slate-500 dark:text-slate-400 mb-2">
            {t('explore.needs_location', 'The offline trip pack depends on where you are. Share your location to see them.')}
          </p>
          <LocationStatus loc={loc} />
        </Card>
      )}
      <TwoWayVoiceTranslator />
      <CulturalEtiquetteCard lang={lang} />
      <CurrencyCard />
      <FairPriceCard touristId={tid} />
    </div>
  )
}
