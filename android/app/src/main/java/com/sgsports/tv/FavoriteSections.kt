package com.sgsports.tv

import org.json.JSONObject

/**
 * Favorite membership and Home section placement, kept free of Android/Compose so it is unit-testable.
 *
 * A favorite is identified by its canonical team ID. An event belongs to a favorite when either
 * participant's canonical key (the backend's `sides`, else the ESPN score block's two names folded
 * through the same [teamKey] the backend uses) equals the favorite's key within the favorite's own
 * sport and league -- never a display-name substring and never a list position. The older
 * title-pattern rule is kept as a fallback for events that carry no participant data.
 */

/** The favorites of the active profile, in the profile's order. Without a saved favorites field (no prefs yet, or a pre-migration profile) the installed list stands, as before; an explicitly empty list means no favorites. */
fun activeFavoriteTeams(installed:List<Team>,prefs:PrefsUi):List<Team>{
 val data=when(prefs){is PrefsUi.Ready->prefs.data;is PrefsUi.Conflict->prefs.current;else->null}
 val saved=data?.prefs?.optJSONArray("favorites")?:return installed
 val ids=(0 until saved.length()).map{saved.optString(it)}
 val byId=installed.associateBy{it.id}
 return ids.distinct().mapNotNull{byId[it]}
}

/** Canonical participant keys of an event; empty when the event names none. */
fun participantKeys(g:Game):Set<String>{
 val sides=g.raw.strings("sides").filter{it.isNotBlank()}
 if(sides.size==2)return sides.toSet()
 val live=g.live
 if(live!=null&&live.home.isNotBlank()&&live.away.isNotBlank())return setOf(teamKey(live.home),teamKey(live.away))
 return emptySet()
}

/** Whether [g] is a game of [team]: same sport and league scope, and either participant is the team. */
fun isFavoriteEvent(team:Team,g:Game):Boolean{
 if(g.sport!=team.sport||!(team.anyLeague||g.league==team.league))return false
 val keys=participantKeys(g)
 if(keys.isNotEmpty())return teamKey(team.name) in keys||matches(team,g)
 return matches(team,g)
}

/** One real event, whatever number of guide airings or merged cards carry it: sport, league, the unordered participants and the kickoff slot. Two games between the same teams more than 30 minutes apart (a doubleheader) are different events. */
fun canonicalEventKey(g:Game):String{
 val keys=participantKeys(g).sorted()
 if(keys.size!=2)return "id:"+g.id
 val slot=g.startEpoch()/1800
 return listOf(g.sport,g.league,keys.joinToString("|"),slot.toString()).joinToString("/")
}

data class HomeSections(val favoriteLive:List<Game>,val worthWatching:List<Game>)

/**
 * "Your teams live" and "Worth watching" for the active profile. A favorite event is never also
 * worth watching, and an event carried by two cards appears once.
 */
fun homeSections(programs:List<Game>,favorites:List<Team>):HomeSections{
 val live=programs.filter{it.phase()==GamePhase.LIVE}
 val favorite=live.filter{g->favorites.any{isFavoriteEvent(it,g)}}
 val favoriteKeys=favorite.map(::canonicalEventKey).toSet()
 val watch=live.filter{it.live?.notable==true&&it !in favorite&&canonicalEventKey(it) !in favoriteKeys}
 // Only eligible candidates are reordered: higher backend score first, then the canonical event key
 // so ties are reproducible. Games with no usable relevance keep their source order.
 val ordered=watch.sortedWith(compareByDescending<Game>{it.live?.relevance?.score?:0}.thenBy{if((it.live?.relevance?.score?:0)>0)canonicalEventKey(it) else ""})
 return HomeSections(favorite.distinctBy(::canonicalEventKey),ordered.distinctBy(::canonicalEventKey))
}
