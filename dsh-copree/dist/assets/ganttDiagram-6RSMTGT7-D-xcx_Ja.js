import{g as Re,s as Ve,q as He,p as qe,a as Be,b as Ge,_ as c,c as ft,d as bt,e as Xe,aR as X,l as ot,k as je,j as Ue,z as Ze,u as Qe}from"./mermaid.core-Dfw2ypXu.js";import{aG as At}from"./index-BpIXl2qS.js";import{t as Ke,m as Je,a as tr,b as ae,c as oe,d as er,e as rr,f as ir,g as nr,h as sr,i as ar,j as or,k as ce,l as le,n as ue,s as de,o as fe}from"./time-DAeG9hya.js";import{l as cr}from"./linear-BkEJkjA2.js";import{R as Te,r as lr,o as be,q as we,C as _e,u as Ot,v as ur}from"./step-B6EOmCqb.js";import"./init-Dmth1JHB.js";import"./defaultLocale-DX6XiGOO.js";function dr(t){return t}var _t=1,Wt=2,Ht=3,wt=4,he=1e-6;function fr(t){return"translate("+t+",0)"}function hr(t){return"translate(0,"+t+")"}function mr(t){return e=>+t(e)}function kr(t,e){return e=Math.max(0,t.bandwidth()-e*2)/2,t.round()&&(e=Math.round(e)),n=>+t(n)+e}function yr(){return!this.__axis}function De(t,e){var n=[],i=null,a=null,f=6,d=6,T=3,M=typeof window<"u"&&window.devicePixelRatio>1?0:.5,$=t===_t||t===wt?-1:1,b=t===wt||t===Wt?"x":"y",L=t===_t||t===Ht?fr:hr;function C(D){var R=i??(e.ticks?e.ticks.apply(e,n):e.domain()),I=a??(e.tickFormat?e.tickFormat.apply(e,n):dr),S=Math.max(f,0)+T,E=e.range(),W=+E[0]+M,A=+E[E.length-1]+M,V=(e.bandwidth?kr:mr)(e.copy(),M),H=D.selection?D.selection():D,Y=H.selectAll(".domain").data([null]),p=H.selectAll(".tick").data(R,e).order(),h=p.exit(),u=p.enter().append("g").attr("class","tick"),x=p.select("line"),v=p.select("text");Y=Y.merge(Y.enter().insert("path",".tick").attr("class","domain").attr("stroke","currentColor")),p=p.merge(u),x=x.merge(u.append("line").attr("stroke","currentColor").attr(b+"2",$*f)),v=v.merge(u.append("text").attr("fill","currentColor").attr(b,$*S).attr("dy",t===_t?"0em":t===Ht?"0.71em":"0.32em")),D!==H&&(Y=Y.transition(D),p=p.transition(D),x=x.transition(D),v=v.transition(D),h=h.transition(D).attr("opacity",he).attr("transform",function(k){return isFinite(k=V(k))?L(k+M):this.getAttribute("transform")}),u.attr("opacity",he).attr("transform",function(k){var m=this.parentNode.__axis;return L((m&&isFinite(m=m(k))?m:V(k))+M)})),h.remove(),Y.attr("d",t===wt||t===Wt?d?"M"+$*d+","+W+"H"+M+"V"+A+"H"+$*d:"M"+M+","+W+"V"+A:d?"M"+W+","+$*d+"V"+M+"H"+A+"V"+$*d:"M"+W+","+M+"H"+A),p.attr("opacity",1).attr("transform",function(k){return L(V(k)+M)}),x.attr(b+"2",$*f),v.attr(b,$*S).text(I),H.filter(yr).attr("fill","none").attr("font-size",10).attr("font-family","sans-serif").attr("text-anchor",t===Wt?"start":t===wt?"end":"middle"),H.each(function(){this.__axis=V})}return C.scale=function(D){return arguments.length?(e=D,C):e},C.ticks=function(){return n=Array.from(arguments),C},C.tickArguments=function(D){return arguments.length?(n=D==null?[]:Array.from(D),C):n.slice()},C.tickValues=function(D){return arguments.length?(i=D==null?null:Array.from(D),C):i&&i.slice()},C.tickFormat=function(D){return arguments.length?(a=D,C):a},C.tickSize=function(D){return arguments.length?(f=d=+D,C):f},C.tickSizeInner=function(D){return arguments.length?(f=+D,C):f},C.tickSizeOuter=function(D){return arguments.length?(d=+D,C):d},C.tickPadding=function(D){return arguments.length?(T=+D,C):T},C.offset=function(D){return arguments.length?(M=+D,C):M},C}function gr(t){return De(_t,t)}function pr(t){return De(Ht,t)}const vr=Math.PI/180,xr=180/Math.PI,Yt=18,Se=.96422,Ce=1,Me=.82521,Ee=4/29,ht=6/29,Ie=3*ht*ht,Tr=ht*ht*ht;function Ye(t){if(t instanceof et)return new et(t.l,t.a,t.b,t.opacity);if(t instanceof it)return $e(t);t instanceof Te||(t=lr(t));var e=Rt(t.r),n=Rt(t.g),i=Rt(t.b),a=Pt((.2225045*e+.7168786*n+.0606169*i)/Ce),f,d;return e===n&&n===i?f=d=a:(f=Pt((.4360747*e+.3850649*n+.1430804*i)/Se),d=Pt((.0139322*e+.0971045*n+.7141733*i)/Me)),new et(116*a-16,500*(f-a),200*(a-d),t.opacity)}function br(t,e,n,i){return arguments.length===1?Ye(t):new et(t,e,n,i??1)}function et(t,e,n,i){this.l=+t,this.a=+e,this.b=+n,this.opacity=+i}be(et,br,we(_e,{brighter(t){return new et(this.l+Yt*(t??1),this.a,this.b,this.opacity)},darker(t){return new et(this.l-Yt*(t??1),this.a,this.b,this.opacity)},rgb(){var t=(this.l+16)/116,e=isNaN(this.a)?t:t+this.a/500,n=isNaN(this.b)?t:t-this.b/200;return e=Se*Nt(e),t=Ce*Nt(t),n=Me*Nt(n),new Te(zt(3.1338561*e-1.6168667*t-.4906146*n),zt(-.9787684*e+1.9161415*t+.033454*n),zt(.0719453*e-.2289914*t+1.4052427*n),this.opacity)}}));function Pt(t){return t>Tr?Math.pow(t,1/3):t/Ie+Ee}function Nt(t){return t>ht?t*t*t:Ie*(t-Ee)}function zt(t){return 255*(t<=.0031308?12.92*t:1.055*Math.pow(t,1/2.4)-.055)}function Rt(t){return(t/=255)<=.04045?t/12.92:Math.pow((t+.055)/1.055,2.4)}function wr(t){if(t instanceof it)return new it(t.h,t.c,t.l,t.opacity);if(t instanceof et||(t=Ye(t)),t.a===0&&t.b===0)return new it(NaN,0<t.l&&t.l<100?0:NaN,t.l,t.opacity);var e=Math.atan2(t.b,t.a)*xr;return new it(e<0?e+360:e,Math.sqrt(t.a*t.a+t.b*t.b),t.l,t.opacity)}function qt(t,e,n,i){return arguments.length===1?wr(t):new it(t,e,n,i??1)}function it(t,e,n,i){this.h=+t,this.c=+e,this.l=+n,this.opacity=+i}function $e(t){if(isNaN(t.h))return new et(t.l,0,0,t.opacity);var e=t.h*vr;return new et(t.l,Math.cos(e)*t.c,Math.sin(e)*t.c,t.opacity)}be(it,qt,we(_e,{brighter(t){return new it(this.h,this.c,this.l+Yt*(t??1),this.opacity)},darker(t){return new it(this.h,this.c,this.l-Yt*(t??1),this.opacity)},rgb(){return $e(this).rgb()}}));function _r(t){return function(e,n){var i=t((e=qt(e)).h,(n=qt(n)).h),a=Ot(e.c,n.c),f=Ot(e.l,n.l),d=Ot(e.opacity,n.opacity);return function(T){return e.h=i(T),e.c=a(T),e.l=f(T),e.opacity=d(T),e+""}}}const Dr=_r(ur);var Dt={exports:{}},Sr=Dt.exports,me;function Cr(){return me||(me=1,(function(t,e){(function(n,i){t.exports=i()})(Sr,(function(){var n="day";return function(i,a,f){var d=function($){return $.add(4-$.isoWeekday(),n)},T=a.prototype;T.isoWeekYear=function(){return d(this).year()},T.isoWeek=function($){if(!this.$utils().u($))return this.add(7*($-this.isoWeek()),n);var b,L,C,D,R=d(this),I=(b=this.isoWeekYear(),L=this.$u,C=(L?f.utc:f)().year(b).startOf("year"),D=4-C.isoWeekday(),C.isoWeekday()>4&&(D+=7),C.add(D,n));return R.diff(I,"week")+1},T.isoWeekday=function($){return this.$utils().u($)?this.day()||7:this.day(this.day()%7?$:$-7)};var M=T.startOf;T.startOf=function($,b){var L=this.$utils(),C=!!L.u(b)||b;return L.p($)==="isoweek"?C?this.date(this.date()-(this.isoWeekday()-1)).startOf("day"):this.date(this.date()-1-(this.isoWeekday()-1)+7).endOf("day"):M.bind(this)($,b)}}}))})(Dt)),Dt.exports}var Mr=Cr();const Er=At(Mr);var St={exports:{}},Ir=St.exports,ke;function Yr(){return ke||(ke=1,(function(t,e){(function(n,i){t.exports=i()})(Ir,(function(){var n={LTS:"h:mm:ss A",LT:"h:mm A",L:"MM/DD/YYYY",LL:"MMMM D, YYYY",LLL:"MMMM D, YYYY h:mm A",LLLL:"dddd, MMMM D, YYYY h:mm A"},i=/(\[[^[]*\])|([-_:/.,()\s]+)|(A|a|Q|YYYY|YY?|ww?|MM?M?M?|Do|DD?|hh?|HH?|mm?|ss?|S{1,3}|z|ZZ?)/g,a=/\d/,f=/\d\d/,d=/\d\d?/,T=/\d*[^-_:/,()\s\d]+/,M={},$=function(S){return(S=+S)+(S>68?1900:2e3)},b=function(S){return function(E){this[S]=+E}},L=[/[+-]\d\d:?(\d\d)?|Z/,function(S){(this.zone||(this.zone={})).offset=(function(E){if(!E||E==="Z")return 0;var W=E.match(/([+-]|\d\d)/g),A=60*W[1]+(+W[2]||0);return A===0?0:W[0]==="+"?-A:A})(S)}],C=function(S){var E=M[S];return E&&(E.indexOf?E:E.s.concat(E.f))},D=function(S,E){var W,A=M.meridiem;if(A){for(var V=1;V<=24;V+=1)if(S.indexOf(A(V,0,E))>-1){W=V>12;break}}else W=S===(E?"pm":"PM");return W},R={A:[T,function(S){this.afternoon=D(S,!1)}],a:[T,function(S){this.afternoon=D(S,!0)}],Q:[a,function(S){this.month=3*(S-1)+1}],S:[a,function(S){this.milliseconds=100*+S}],SS:[f,function(S){this.milliseconds=10*+S}],SSS:[/\d{3}/,function(S){this.milliseconds=+S}],s:[d,b("seconds")],ss:[d,b("seconds")],m:[d,b("minutes")],mm:[d,b("minutes")],H:[d,b("hours")],h:[d,b("hours")],HH:[d,b("hours")],hh:[d,b("hours")],D:[d,b("day")],DD:[f,b("day")],Do:[T,function(S){var E=M.ordinal,W=S.match(/\d+/);if(this.day=W[0],E)for(var A=1;A<=31;A+=1)E(A).replace(/\[|\]/g,"")===S&&(this.day=A)}],w:[d,b("week")],ww:[f,b("week")],M:[d,b("month")],MM:[f,b("month")],MMM:[T,function(S){var E=C("months"),W=(C("monthsShort")||E.map((function(A){return A.slice(0,3)}))).indexOf(S)+1;if(W<1)throw new Error;this.month=W%12||W}],MMMM:[T,function(S){var E=C("months").indexOf(S)+1;if(E<1)throw new Error;this.month=E%12||E}],Y:[/[+-]?\d+/,b("year")],YY:[f,function(S){this.year=$(S)}],YYYY:[/\d{4}/,b("year")],Z:L,ZZ:L};function I(S){var E,W;E=S,W=M&&M.formats;for(var A=(S=E.replace(/(\[[^\]]+])|(LTS?|l{1,4}|L{1,4})/g,(function(x,v,k){var m=k&&k.toUpperCase();return v||W[k]||n[k]||W[m].replace(/(\[[^\]]+])|(MMMM|MM|DD|dddd)/g,(function(o,l,y){return l||y.slice(1)}))}))).match(i),V=A.length,H=0;H<V;H+=1){var Y=A[H],p=R[Y],h=p&&p[0],u=p&&p[1];A[H]=u?{regex:h,parser:u}:Y.replace(/^\[|\]$/g,"")}return function(x){for(var v={},k=0,m=0;k<V;k+=1){var o=A[k];if(typeof o=="string")m+=o.length;else{var l=o.regex,y=o.parser,g=x.slice(m),w=l.exec(g)[0];y.call(v,w),x=x.replace(w,"")}}return(function(s){var z=s.afternoon;if(z!==void 0){var r=s.hours;z?r<12&&(s.hours+=12):r===12&&(s.hours=0),delete s.afternoon}})(v),v}}return function(S,E,W){W.p.customParseFormat=!0,S&&S.parseTwoDigitYear&&($=S.parseTwoDigitYear);var A=E.prototype,V=A.parse;A.parse=function(H){var Y=H.date,p=H.utc,h=H.args;this.$u=p;var u=h[1];if(typeof u=="string"){var x=h[2]===!0,v=h[3]===!0,k=x||v,m=h[2];v&&(m=h[2]),M=this.$locale(),!x&&m&&(M=W.Ls[m]),this.$d=(function(g,w,s,z){try{if(["x","X"].indexOf(w)>-1)return new Date((w==="X"?1e3:1)*g);var r=I(w)(g),_=r.year,N=r.month,P=r.day,O=r.hours,G=r.minutes,F=r.seconds,Q=r.milliseconds,nt=r.zone,lt=r.week,yt=new Date,gt=P||(_||N?1:yt.getDate()),ut=_||yt.getFullYear(),q=0;_&&!N||(q=N>0?N-1:yt.getMonth());var Z,j=O||0,at=G||0,K=F||0,st=Q||0;return nt?new Date(Date.UTC(ut,q,gt,j,at,K,st+60*nt.offset*1e3)):s?new Date(Date.UTC(ut,q,gt,j,at,K,st)):(Z=new Date(ut,q,gt,j,at,K,st),lt&&(Z=z(Z).week(lt).toDate()),Z)}catch{return new Date("")}})(Y,u,p,W),this.init(),m&&m!==!0&&(this.$L=this.locale(m).$L),k&&Y!=this.format(u)&&(this.$d=new Date("")),M={}}else if(u instanceof Array)for(var o=u.length,l=1;l<=o;l+=1){h[1]=u[l-1];var y=W.apply(this,h);if(y.isValid()){this.$d=y.$d,this.$L=y.$L,this.init();break}l===o&&(this.$d=new Date(""))}else V.call(this,H)}}}))})(St)),St.exports}var $r=Yr();const Fr=At($r);var Ct={exports:{}},Ar=Ct.exports,ye;function Lr(){return ye||(ye=1,(function(t,e){(function(n,i){t.exports=i()})(Ar,(function(){return function(n,i){var a=i.prototype,f=a.format;a.format=function(d){var T=this,M=this.$locale();if(!this.isValid())return f.bind(this)(d);var $=this.$utils(),b=(d||"YYYY-MM-DDTHH:mm:ssZ").replace(/\[([^\]]+)]|Q|wo|ww|w|WW|W|zzz|z|gggg|GGGG|Do|X|x|k{1,2}|S/g,(function(L){switch(L){case"Q":return Math.ceil((T.$M+1)/3);case"Do":return M.ordinal(T.$D);case"gggg":return T.weekYear();case"GGGG":return T.isoWeekYear();case"wo":return M.ordinal(T.week(),"W");case"w":case"ww":return $.s(T.week(),L==="w"?1:2,"0");case"W":case"WW":return $.s(T.isoWeek(),L==="W"?1:2,"0");case"k":case"kk":return $.s(String(T.$H===0?24:T.$H),L==="k"?1:2,"0");case"X":return Math.floor(T.$d.getTime()/1e3);case"x":return T.$d.getTime();case"z":return"["+T.offsetName()+"]";case"zzz":return"["+T.offsetName("long")+"]";default:return L}}));return f.bind(this)(b)}}}))})(Ct)),Ct.exports}var Or=Lr();const Wr=At(Or);var Mt={exports:{}},Pr=Mt.exports,ge;function Nr(){return ge||(ge=1,(function(t,e){(function(n,i){t.exports=i()})(Pr,(function(){var n,i,a=1e3,f=6e4,d=36e5,T=864e5,M=31536e6,$=2628e6,b=/^(-|\+)?P(?:([-+]?[0-9,.]*)Y)?(?:([-+]?[0-9,.]*)M)?(?:([-+]?[0-9,.]*)W)?(?:([-+]?[0-9,.]*)D)?(?:T(?:([-+]?[0-9,.]*)H)?(?:([-+]?[0-9,.]*)M)?(?:([-+]?[0-9,.]*)S)?)?$/,L=/\[([^\]]+)]|YYYY|YY|Y|M{1,2}|D{1,2}|H{1,2}|m{1,2}|s{1,2}|SSS/g,C={years:M,months:$,days:T,hours:d,minutes:f,seconds:a,milliseconds:1,weeks:6048e5},D=function(Y){return Y instanceof V},R=function(Y,p,h){return new V(Y,h,p.$l)},I=function(Y){return i.p(Y)+"s"},S=function(Y){return Y<0},E=function(Y){return S(Y)?Math.ceil(Y):Math.floor(Y)},W=function(Y){return Math.abs(Y)},A=function(Y,p){return Y?S(Y)?{negative:!0,format:""+W(Y)+p}:{negative:!1,format:""+Y+p}:{negative:!1,format:""}},V=(function(){function Y(h,u,x){var v=this;if(this.$d={},this.$l=x,h===void 0&&(this.$ms=0,this.parseFromMilliseconds()),u)return R(h*C[I(u)],this);if(typeof h=="number")return this.$ms=h,this.parseFromMilliseconds(),this;if(typeof h=="object")return Object.keys(h).forEach((function(o){v.$d[I(o)]=h[o]})),this.calMilliseconds(),this;if(typeof h=="string"){var k=h.match(b);if(k){var m=k.slice(2).map((function(o){return o!=null?Number(o):0}));return this.$d.years=m[0],this.$d.months=m[1],this.$d.weeks=m[2],this.$d.days=m[3],this.$d.hours=m[4],this.$d.minutes=m[5],this.$d.seconds=m[6],this.calMilliseconds(),this}}return this}var p=Y.prototype;return p.calMilliseconds=function(){var h=this;this.$ms=Object.keys(this.$d).reduce((function(u,x){return u+(h.$d[x]||0)*C[x]}),0)},p.parseFromMilliseconds=function(){var h=this.$ms;this.$d.years=E(h/M),h%=M,this.$d.months=E(h/$),h%=$,this.$d.days=E(h/T),h%=T,this.$d.hours=E(h/d),h%=d,this.$d.minutes=E(h/f),h%=f,this.$d.seconds=E(h/a),h%=a,this.$d.milliseconds=h},p.toISOString=function(){var h=A(this.$d.years,"Y"),u=A(this.$d.months,"M"),x=+this.$d.days||0;this.$d.weeks&&(x+=7*this.$d.weeks);var v=A(x,"D"),k=A(this.$d.hours,"H"),m=A(this.$d.minutes,"M"),o=this.$d.seconds||0;this.$d.milliseconds&&(o+=this.$d.milliseconds/1e3,o=Math.round(1e3*o)/1e3);var l=A(o,"S"),y=h.negative||u.negative||v.negative||k.negative||m.negative||l.negative,g=k.format||m.format||l.format?"T":"",w=(y?"-":"")+"P"+h.format+u.format+v.format+g+k.format+m.format+l.format;return w==="P"||w==="-P"?"P0D":w},p.toJSON=function(){return this.toISOString()},p.format=function(h){var u=h||"YYYY-MM-DDTHH:mm:ss",x={Y:this.$d.years,YY:i.s(this.$d.years,2,"0"),YYYY:i.s(this.$d.years,4,"0"),M:this.$d.months,MM:i.s(this.$d.months,2,"0"),D:this.$d.days,DD:i.s(this.$d.days,2,"0"),H:this.$d.hours,HH:i.s(this.$d.hours,2,"0"),m:this.$d.minutes,mm:i.s(this.$d.minutes,2,"0"),s:this.$d.seconds,ss:i.s(this.$d.seconds,2,"0"),SSS:i.s(this.$d.milliseconds,3,"0")};return u.replace(L,(function(v,k){return k||String(x[v])}))},p.as=function(h){return this.$ms/C[I(h)]},p.get=function(h){var u=this.$ms,x=I(h);return x==="milliseconds"?u%=1e3:u=x==="weeks"?E(u/C[x]):this.$d[x],u||0},p.add=function(h,u,x){var v;return v=u?h*C[I(u)]:D(h)?h.$ms:R(h,this).$ms,R(this.$ms+v*(x?-1:1),this)},p.subtract=function(h,u){return this.add(h,u,!0)},p.locale=function(h){var u=this.clone();return u.$l=h,u},p.clone=function(){return R(this.$ms,this)},p.humanize=function(h){return n().add(this.$ms,"ms").locale(this.$l).fromNow(!h)},p.valueOf=function(){return this.asMilliseconds()},p.milliseconds=function(){return this.get("milliseconds")},p.asMilliseconds=function(){return this.as("milliseconds")},p.seconds=function(){return this.get("seconds")},p.asSeconds=function(){return this.as("seconds")},p.minutes=function(){return this.get("minutes")},p.asMinutes=function(){return this.as("minutes")},p.hours=function(){return this.get("hours")},p.asHours=function(){return this.as("hours")},p.days=function(){return this.get("days")},p.asDays=function(){return this.as("days")},p.weeks=function(){return this.get("weeks")},p.asWeeks=function(){return this.as("weeks")},p.months=function(){return this.get("months")},p.asMonths=function(){return this.as("months")},p.years=function(){return this.get("years")},p.asYears=function(){return this.as("years")},Y})(),H=function(Y,p,h){return Y.add(p.years()*h,"y").add(p.months()*h,"M").add(p.days()*h,"d").add(p.hours()*h,"h").add(p.minutes()*h,"m").add(p.seconds()*h,"s").add(p.milliseconds()*h,"ms")};return function(Y,p,h){n=h,i=h().$utils(),h.duration=function(v,k){var m=h.locale();return R(v,{$l:m},k)},h.isDuration=D;var u=p.prototype.add,x=p.prototype.subtract;p.prototype.add=function(v,k){return D(v)?H(this,v,1):u.bind(this)(v,k)},p.prototype.subtract=function(v,k){return D(v)?H(this,v,-1):x.bind(this)(v,k)}}}))})(Mt)),Mt.exports}var zr=Nr();const Rr=At(zr);var Bt=(function(){var t=c(function(m,o,l,y){for(l=l||{},y=m.length;y--;l[m[y]]=o);return l},"o"),e=[6,8,10,12,13,14,15,16,17,18,20,21,22,23,24,25,26,27,28,29,30,31,33,35,36,38,40],n=[1,26],i=[1,27],a=[1,28],f=[1,29],d=[1,30],T=[1,31],M=[1,32],$=[1,33],b=[1,34],L=[1,9],C=[1,10],D=[1,11],R=[1,12],I=[1,13],S=[1,14],E=[1,15],W=[1,16],A=[1,19],V=[1,20],H=[1,21],Y=[1,22],p=[1,23],h=[1,25],u=[1,35],x={trace:c(function(){},"trace"),yy:{},symbols_:{error:2,start:3,gantt:4,document:5,EOF:6,line:7,SPACE:8,statement:9,NL:10,weekday:11,weekday_monday:12,weekday_tuesday:13,weekday_wednesday:14,weekday_thursday:15,weekday_friday:16,weekday_saturday:17,weekday_sunday:18,weekend:19,weekend_friday:20,weekend_saturday:21,dateFormat:22,inclusiveEndDates:23,topAxis:24,axisFormat:25,tickInterval:26,excludes:27,includes:28,todayMarker:29,title:30,acc_title:31,acc_title_value:32,acc_descr:33,acc_descr_value:34,acc_descr_multiline_value:35,section:36,clickStatement:37,taskTxt:38,taskData:39,click:40,callbackname:41,callbackargs:42,href:43,clickStatementDebug:44,$accept:0,$end:1},terminals_:{2:"error",4:"gantt",6:"EOF",8:"SPACE",10:"NL",12:"weekday_monday",13:"weekday_tuesday",14:"weekday_wednesday",15:"weekday_thursday",16:"weekday_friday",17:"weekday_saturday",18:"weekday_sunday",20:"weekend_friday",21:"weekend_saturday",22:"dateFormat",23:"inclusiveEndDates",24:"topAxis",25:"axisFormat",26:"tickInterval",27:"excludes",28:"includes",29:"todayMarker",30:"title",31:"acc_title",32:"acc_title_value",33:"acc_descr",34:"acc_descr_value",35:"acc_descr_multiline_value",36:"section",38:"taskTxt",39:"taskData",40:"click",41:"callbackname",42:"callbackargs",43:"href"},productions_:[0,[3,3],[5,0],[5,2],[7,2],[7,1],[7,1],[7,1],[11,1],[11,1],[11,1],[11,1],[11,1],[11,1],[11,1],[19,1],[19,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,2],[9,2],[9,1],[9,1],[9,1],[9,2],[37,2],[37,3],[37,3],[37,4],[37,3],[37,4],[37,2],[44,2],[44,3],[44,3],[44,4],[44,3],[44,4],[44,2]],performAction:c(function(o,l,y,g,w,s,z){var r=s.length-1;switch(w){case 1:return s[r-1];case 2:this.$=[];break;case 3:s[r-1].push(s[r]),this.$=s[r-1];break;case 4:case 5:this.$=s[r];break;case 6:case 7:this.$=[];break;case 8:g.setWeekday("monday");break;case 9:g.setWeekday("tuesday");break;case 10:g.setWeekday("wednesday");break;case 11:g.setWeekday("thursday");break;case 12:g.setWeekday("friday");break;case 13:g.setWeekday("saturday");break;case 14:g.setWeekday("sunday");break;case 15:g.setWeekend("friday");break;case 16:g.setWeekend("saturday");break;case 17:g.setDateFormat(s[r].substr(11)),this.$=s[r].substr(11);break;case 18:g.enableInclusiveEndDates(),this.$=s[r].substr(18);break;case 19:g.TopAxis(),this.$=s[r].substr(8);break;case 20:g.setAxisFormat(s[r].substr(11)),this.$=s[r].substr(11);break;case 21:g.setTickInterval(s[r].substr(13)),this.$=s[r].substr(13);break;case 22:g.setExcludes(s[r].substr(9)),this.$=s[r].substr(9);break;case 23:g.setIncludes(s[r].substr(9)),this.$=s[r].substr(9);break;case 24:g.setTodayMarker(s[r].substr(12)),this.$=s[r].substr(12);break;case 27:g.setDiagramTitle(s[r].substr(6)),this.$=s[r].substr(6);break;case 28:this.$=s[r].trim(),g.setAccTitle(this.$);break;case 29:case 30:this.$=s[r].trim(),g.setAccDescription(this.$);break;case 31:g.addSection(s[r].substr(8)),this.$=s[r].substr(8);break;case 33:g.addTask(s[r-1],s[r]),this.$="task";break;case 34:this.$=s[r-1],g.setClickEvent(s[r-1],s[r],null);break;case 35:this.$=s[r-2],g.setClickEvent(s[r-2],s[r-1],s[r]);break;case 36:this.$=s[r-2],g.setClickEvent(s[r-2],s[r-1],null),g.setLink(s[r-2],s[r]);break;case 37:this.$=s[r-3],g.setClickEvent(s[r-3],s[r-2],s[r-1]),g.setLink(s[r-3],s[r]);break;case 38:this.$=s[r-2],g.setClickEvent(s[r-2],s[r],null),g.setLink(s[r-2],s[r-1]);break;case 39:this.$=s[r-3],g.setClickEvent(s[r-3],s[r-1],s[r]),g.setLink(s[r-3],s[r-2]);break;case 40:this.$=s[r-1],g.setLink(s[r-1],s[r]);break;case 41:case 47:this.$=s[r-1]+" "+s[r];break;case 42:case 43:case 45:this.$=s[r-2]+" "+s[r-1]+" "+s[r];break;case 44:case 46:this.$=s[r-3]+" "+s[r-2]+" "+s[r-1]+" "+s[r];break}},"anonymous"),table:[{3:1,4:[1,2]},{1:[3]},t(e,[2,2],{5:3}),{6:[1,4],7:5,8:[1,6],9:7,10:[1,8],11:17,12:n,13:i,14:a,15:f,16:d,17:T,18:M,19:18,20:$,21:b,22:L,23:C,24:D,25:R,26:I,27:S,28:E,29:W,30:A,31:V,33:H,35:Y,36:p,37:24,38:h,40:u},t(e,[2,7],{1:[2,1]}),t(e,[2,3]),{9:36,11:17,12:n,13:i,14:a,15:f,16:d,17:T,18:M,19:18,20:$,21:b,22:L,23:C,24:D,25:R,26:I,27:S,28:E,29:W,30:A,31:V,33:H,35:Y,36:p,37:24,38:h,40:u},t(e,[2,5]),t(e,[2,6]),t(e,[2,17]),t(e,[2,18]),t(e,[2,19]),t(e,[2,20]),t(e,[2,21]),t(e,[2,22]),t(e,[2,23]),t(e,[2,24]),t(e,[2,25]),t(e,[2,26]),t(e,[2,27]),{32:[1,37]},{34:[1,38]},t(e,[2,30]),t(e,[2,31]),t(e,[2,32]),{39:[1,39]},t(e,[2,8]),t(e,[2,9]),t(e,[2,10]),t(e,[2,11]),t(e,[2,12]),t(e,[2,13]),t(e,[2,14]),t(e,[2,15]),t(e,[2,16]),{41:[1,40],43:[1,41]},t(e,[2,4]),t(e,[2,28]),t(e,[2,29]),t(e,[2,33]),t(e,[2,34],{42:[1,42],43:[1,43]}),t(e,[2,40],{41:[1,44]}),t(e,[2,35],{43:[1,45]}),t(e,[2,36]),t(e,[2,38],{42:[1,46]}),t(e,[2,37]),t(e,[2,39])],defaultActions:{},parseError:c(function(o,l){if(l.recoverable)this.trace(o);else{var y=new Error(o);throw y.hash=l,y}},"parseError"),parse:c(function(o){var l=this,y=[0],g=[],w=[null],s=[],z=this.table,r="",_=0,N=0,P=2,O=1,G=s.slice.call(arguments,1),F=Object.create(this.lexer),Q={yy:{}};for(var nt in this.yy)Object.prototype.hasOwnProperty.call(this.yy,nt)&&(Q.yy[nt]=this.yy[nt]);F.setInput(o,Q.yy),Q.yy.lexer=F,Q.yy.parser=this,typeof F.yylloc>"u"&&(F.yylloc={});var lt=F.yylloc;s.push(lt);var yt=F.options&&F.options.ranges;typeof Q.yy.parseError=="function"?this.parseError=Q.yy.parseError:this.parseError=Object.getPrototypeOf(this).parseError;function gt(U){y.length=y.length-2*U,w.length=w.length-U,s.length=s.length-U}c(gt,"popStack");function ut(){var U;return U=g.pop()||F.lex()||O,typeof U!="number"&&(U instanceof Array&&(g=U,U=g.pop()),U=l.symbols_[U]||U),U}c(ut,"lex");for(var q,Z,j,at,K={},st,J,se,Tt;;){if(Z=y[y.length-1],this.defaultActions[Z]?j=this.defaultActions[Z]:((q===null||typeof q>"u")&&(q=ut()),j=z[Z]&&z[Z][q]),typeof j>"u"||!j.length||!j[0]){var Lt="";Tt=[];for(st in z[Z])this.terminals_[st]&&st>P&&Tt.push("'"+this.terminals_[st]+"'");F.showPosition?Lt="Parse error on line "+(_+1)+`:
`+F.showPosition()+`
Expecting `+Tt.join(", ")+", got '"+(this.terminals_[q]||q)+"'":Lt="Parse error on line "+(_+1)+": Unexpected "+(q==O?"end of input":"'"+(this.terminals_[q]||q)+"'"),this.parseError(Lt,{text:F.match,token:this.terminals_[q]||q,line:F.yylineno,loc:lt,expected:Tt})}if(j[0]instanceof Array&&j.length>1)throw new Error("Parse Error: multiple actions possible at state: "+Z+", token: "+q);switch(j[0]){case 1:y.push(q),w.push(F.yytext),s.push(F.yylloc),y.push(j[1]),q=null,N=F.yyleng,r=F.yytext,_=F.yylineno,lt=F.yylloc;break;case 2:if(J=this.productions_[j[1]][1],K.$=w[w.length-J],K._$={first_line:s[s.length-(J||1)].first_line,last_line:s[s.length-1].last_line,first_column:s[s.length-(J||1)].first_column,last_column:s[s.length-1].last_column},yt&&(K._$.range=[s[s.length-(J||1)].range[0],s[s.length-1].range[1]]),at=this.performAction.apply(K,[r,N,_,Q.yy,j[1],w,s].concat(G)),typeof at<"u")return at;J&&(y=y.slice(0,-1*J*2),w=w.slice(0,-1*J),s=s.slice(0,-1*J)),y.push(this.productions_[j[1]][0]),w.push(K.$),s.push(K._$),se=z[y[y.length-2]][y[y.length-1]],y.push(se);break;case 3:return!0}}return!0},"parse")},v=(function(){var m={EOF:1,parseError:c(function(l,y){if(this.yy.parser)this.yy.parser.parseError(l,y);else throw new Error(l)},"parseError"),setInput:c(function(o,l){return this.yy=l||this.yy||{},this._input=o,this._more=this._backtrack=this.done=!1,this.yylineno=this.yyleng=0,this.yytext=this.matched=this.match="",this.conditionStack=["INITIAL"],this.yylloc={first_line:1,first_column:0,last_line:1,last_column:0},this.options.ranges&&(this.yylloc.range=[0,0]),this.offset=0,this},"setInput"),input:c(function(){var o=this._input[0];this.yytext+=o,this.yyleng++,this.offset++,this.match+=o,this.matched+=o;var l=o.match(/(?:\r\n?|\n).*/g);return l?(this.yylineno++,this.yylloc.last_line++):this.yylloc.last_column++,this.options.ranges&&this.yylloc.range[1]++,this._input=this._input.slice(1),o},"input"),unput:c(function(o){var l=o.length,y=o.split(/(?:\r\n?|\n)/g);this._input=o+this._input,this.yytext=this.yytext.substr(0,this.yytext.length-l),this.offset-=l;var g=this.match.split(/(?:\r\n?|\n)/g);this.match=this.match.substr(0,this.match.length-1),this.matched=this.matched.substr(0,this.matched.length-1),y.length-1&&(this.yylineno-=y.length-1);var w=this.yylloc.range;return this.yylloc={first_line:this.yylloc.first_line,last_line:this.yylineno+1,first_column:this.yylloc.first_column,last_column:y?(y.length===g.length?this.yylloc.first_column:0)+g[g.length-y.length].length-y[0].length:this.yylloc.first_column-l},this.options.ranges&&(this.yylloc.range=[w[0],w[0]+this.yyleng-l]),this.yyleng=this.yytext.length,this},"unput"),more:c(function(){return this._more=!0,this},"more"),reject:c(function(){if(this.options.backtrack_lexer)this._backtrack=!0;else return this.parseError("Lexical error on line "+(this.yylineno+1)+`. You can only invoke reject() in the lexer when the lexer is of the backtracking persuasion (options.backtrack_lexer = true).
`+this.showPosition(),{text:"",token:null,line:this.yylineno});return this},"reject"),less:c(function(o){this.unput(this.match.slice(o))},"less"),pastInput:c(function(){var o=this.matched.substr(0,this.matched.length-this.match.length);return(o.length>20?"...":"")+o.substr(-20).replace(/\n/g,"")},"pastInput"),upcomingInput:c(function(){var o=this.match;return o.length<20&&(o+=this._input.substr(0,20-o.length)),(o.substr(0,20)+(o.length>20?"...":"")).replace(/\n/g,"")},"upcomingInput"),showPosition:c(function(){var o=this.pastInput(),l=new Array(o.length+1).join("-");return o+this.upcomingInput()+`
`+l+"^"},"showPosition"),test_match:c(function(o,l){var y,g,w;if(this.options.backtrack_lexer&&(w={yylineno:this.yylineno,yylloc:{first_line:this.yylloc.first_line,last_line:this.last_line,first_column:this.yylloc.first_column,last_column:this.yylloc.last_column},yytext:this.yytext,match:this.match,matches:this.matches,matched:this.matched,yyleng:this.yyleng,offset:this.offset,_more:this._more,_input:this._input,yy:this.yy,conditionStack:this.conditionStack.slice(0),done:this.done},this.options.ranges&&(w.yylloc.range=this.yylloc.range.slice(0))),g=o[0].match(/(?:\r\n?|\n).*/g),g&&(this.yylineno+=g.length),this.yylloc={first_line:this.yylloc.last_line,last_line:this.yylineno+1,first_column:this.yylloc.last_column,last_column:g?g[g.length-1].length-g[g.length-1].match(/\r?\n?/)[0].length:this.yylloc.last_column+o[0].length},this.yytext+=o[0],this.match+=o[0],this.matches=o,this.yyleng=this.yytext.length,this.options.ranges&&(this.yylloc.range=[this.offset,this.offset+=this.yyleng]),this._more=!1,this._backtrack=!1,this._input=this._input.slice(o[0].length),this.matched+=o[0],y=this.performAction.call(this,this.yy,this,l,this.conditionStack[this.conditionStack.length-1]),this.done&&this._input&&(this.done=!1),y)return y;if(this._backtrack){for(var s in w)this[s]=w[s];return!1}return!1},"test_match"),next:c(function(){if(this.done)return this.EOF;this._input||(this.done=!0);var o,l,y,g;this._more||(this.yytext="",this.match="");for(var w=this._currentRules(),s=0;s<w.length;s++)if(y=this._input.match(this.rules[w[s]]),y&&(!l||y[0].length>l[0].length)){if(l=y,g=s,this.options.backtrack_lexer){if(o=this.test_match(y,w[s]),o!==!1)return o;if(this._backtrack){l=!1;continue}else return!1}else if(!this.options.flex)break}return l?(o=this.test_match(l,w[g]),o!==!1?o:!1):this._input===""?this.EOF:this.parseError("Lexical error on line "+(this.yylineno+1)+`. Unrecognized text.
`+this.showPosition(),{text:"",token:null,line:this.yylineno})},"next"),lex:c(function(){var l=this.next();return l||this.lex()},"lex"),begin:c(function(l){this.conditionStack.push(l)},"begin"),popState:c(function(){var l=this.conditionStack.length-1;return l>0?this.conditionStack.pop():this.conditionStack[0]},"popState"),_currentRules:c(function(){return this.conditionStack.length&&this.conditionStack[this.conditionStack.length-1]?this.conditions[this.conditionStack[this.conditionStack.length-1]].rules:this.conditions.INITIAL.rules},"_currentRules"),topState:c(function(l){return l=this.conditionStack.length-1-Math.abs(l||0),l>=0?this.conditionStack[l]:"INITIAL"},"topState"),pushState:c(function(l){this.begin(l)},"pushState"),stateStackSize:c(function(){return this.conditionStack.length},"stateStackSize"),options:{"case-insensitive":!0},performAction:c(function(l,y,g,w){switch(g){case 0:return this.begin("open_directive"),"open_directive";case 1:return this.begin("acc_title"),31;case 2:return this.popState(),"acc_title_value";case 3:return this.begin("acc_descr"),33;case 4:return this.popState(),"acc_descr_value";case 5:this.begin("acc_descr_multiline");break;case 6:this.popState();break;case 7:return"acc_descr_multiline_value";case 8:break;case 9:break;case 10:break;case 11:return 10;case 12:break;case 13:break;case 14:this.begin("href");break;case 15:this.popState();break;case 16:return 43;case 17:this.begin("callbackname");break;case 18:this.popState();break;case 19:this.popState(),this.begin("callbackargs");break;case 20:return 41;case 21:this.popState();break;case 22:return 42;case 23:this.begin("click");break;case 24:this.popState();break;case 25:return 40;case 26:return 4;case 27:return 22;case 28:return 23;case 29:return 24;case 30:return 25;case 31:return 26;case 32:return 28;case 33:return 27;case 34:return 29;case 35:return 12;case 36:return 13;case 37:return 14;case 38:return 15;case 39:return 16;case 40:return 17;case 41:return 18;case 42:return 20;case 43:return 21;case 44:return"date";case 45:return 30;case 46:return"accDescription";case 47:return 36;case 48:return 38;case 49:return 39;case 50:return":";case 51:return 6;case 52:return"INVALID"}},"anonymous"),rules:[/^(?:%%\{)/i,/^(?:accTitle\s*:\s*)/i,/^(?:(?!\n||)*[^\n]*)/i,/^(?:accDescr\s*:\s*)/i,/^(?:(?!\n||)*[^\n]*)/i,/^(?:accDescr\s*\{\s*)/i,/^(?:[\}])/i,/^(?:[^\}]*)/i,/^(?:%%(?!\{)*[^\n]*)/i,/^(?:[^\}]%%*[^\n]*)/i,/^(?:%%*[^\n]*[\n]*)/i,/^(?:[\n]+)/i,/^(?:\s+)/i,/^(?:%[^\n]*)/i,/^(?:href[\s]+["])/i,/^(?:["])/i,/^(?:[^"]*)/i,/^(?:call[\s]+)/i,/^(?:\([\s]*\))/i,/^(?:\()/i,/^(?:[^(]*)/i,/^(?:\))/i,/^(?:[^)]*)/i,/^(?:click[\s]+)/i,/^(?:[\s\n])/i,/^(?:[^\s\n]*)/i,/^(?:gantt\b)/i,/^(?:dateFormat\s[^#\n;]+)/i,/^(?:inclusiveEndDates\b)/i,/^(?:topAxis\b)/i,/^(?:axisFormat\s[^#\n;]+)/i,/^(?:tickInterval\s[^#\n;]+)/i,/^(?:includes\s[^#\n;]+)/i,/^(?:excludes\s[^#\n;]+)/i,/^(?:todayMarker\s[^\n;]+)/i,/^(?:weekday\s+monday\b)/i,/^(?:weekday\s+tuesday\b)/i,/^(?:weekday\s+wednesday\b)/i,/^(?:weekday\s+thursday\b)/i,/^(?:weekday\s+friday\b)/i,/^(?:weekday\s+saturday\b)/i,/^(?:weekday\s+sunday\b)/i,/^(?:weekend\s+friday\b)/i,/^(?:weekend\s+saturday\b)/i,/^(?:\d\d\d\d-\d\d-\d\d\b)/i,/^(?:title\s[^\n]+)/i,/^(?:accDescription\s[^#\n;]+)/i,/^(?:section\s[^\n]+)/i,/^(?:[^:\n]+)/i,/^(?::[^#\n;]+)/i,/^(?::)/i,/^(?:$)/i,/^(?:.)/i],conditions:{acc_descr_multiline:{rules:[6,7],inclusive:!1},acc_descr:{rules:[4],inclusive:!1},acc_title:{rules:[2],inclusive:!1},callbackargs:{rules:[21,22],inclusive:!1},callbackname:{rules:[18,19,20],inclusive:!1},href:{rules:[15,16],inclusive:!1},click:{rules:[24,25],inclusive:!1},INITIAL:{rules:[0,1,3,5,8,9,10,11,12,13,14,17,23,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52],inclusive:!0}}};return m})();x.lexer=v;function k(){this.yy={}}return c(k,"Parser"),k.prototype=x,x.Parser=k,new k})();Bt.parser=Bt;var Vr=Bt;X.extend(Er);X.extend(Fr);X.extend(Wr);var pe={friday:5,saturday:6},tt="",Ut="",Zt=void 0,Qt="",pt=[],vt=[],Kt=new Map,Jt=[],$t=[],kt="",te="",Fe=["active","done","crit","milestone","vert"],ee=[],dt="",xt=!1,re=!1,ie="sunday",Ft="saturday",Gt=0,Hr=c(function(){Jt=[],$t=[],kt="",ee=[],Et=0,jt=void 0,It=void 0,B=[],tt="",Ut="",te="",Zt=void 0,Qt="",pt=[],vt=[],xt=!1,re=!1,Gt=0,Kt=new Map,dt="",Ze(),ie="sunday",Ft="saturday"},"clear"),qr=c(function(t){dt=t},"setDiagramId"),Br=c(function(t){Ut=t},"setAxisFormat"),Gr=c(function(){return Ut},"getAxisFormat"),Xr=c(function(t){Zt=t},"setTickInterval"),jr=c(function(){return Zt},"getTickInterval"),Ur=c(function(t){Qt=t},"setTodayMarker"),Zr=c(function(){return Qt},"getTodayMarker"),Qr=c(function(t){tt=t},"setDateFormat"),Kr=c(function(){xt=!0},"enableInclusiveEndDates"),Jr=c(function(){return xt},"endDatesAreInclusive"),ti=c(function(){re=!0},"enableTopAxis"),ei=c(function(){return re},"topAxisEnabled"),ri=c(function(t){te=t},"setDisplayMode"),ii=c(function(){return te},"getDisplayMode"),ni=c(function(){return tt},"getDateFormat"),si=c(function(t){pt=t.toLowerCase().split(/[\s,]+/)},"setIncludes"),ai=c(function(){return pt},"getIncludes"),oi=c(function(t){vt=t.toLowerCase().split(/[\s,]+/)},"setExcludes"),ci=c(function(){return vt},"getExcludes"),li=c(function(){return Kt},"getLinks"),ui=c(function(t){kt=t,Jt.push(t)},"addSection"),di=c(function(){return Jt},"getSections"),fi=c(function(){let t=ve();const e=10;let n=0;for(;!t&&n<e;)t=ve(),n++;return $t=B,$t},"getTasks"),Ae=c(function(t,e,n,i){const a=t.format(e.trim()),f=t.format("YYYY-MM-DD");return i.includes(a)||i.includes(f)?!1:n.includes("weekends")&&(t.isoWeekday()===pe[Ft]||t.isoWeekday()===pe[Ft]+1)||n.includes(t.format("dddd").toLowerCase())?!0:n.includes(a)||n.includes(f)},"isInvalidDate"),hi=c(function(t){ie=t},"setWeekday"),mi=c(function(){return ie},"getWeekday"),ki=c(function(t){Ft=t},"setWeekend"),Le=c(function(t,e,n,i){if(!n.length||t.manualEndTime)return;let a;t.startTime instanceof Date?a=X(t.startTime):a=X(t.startTime,e,!0),a=a.add(1,"d");let f;t.endTime instanceof Date?f=X(t.endTime):f=X(t.endTime,e,!0);const[d,T]=yi(a,f,e,n,i);t.endTime=d.toDate(),t.renderEndTime=T},"checkTaskDates"),yi=c(function(t,e,n,i,a){let f=!1,d=null;const T=e.add(1e4,"d");for(;t<=e;){if(f||(d=e.toDate()),f=Ae(t,n,i,a),f&&(e=e.add(1,"d"),e>T))throw new Error("Failed to find a valid date that was not excluded by `excludes` after 10,000 iterations.");t=t.add(1,"d")}return[e,d]},"fixTaskDates"),Xt=c(function(t,e,n){if(n=n.trim(),c(T=>{const M=T.trim();return M==="x"||M==="X"},"isTimestampFormat")(e)&&/^\d+$/.test(n))return new Date(Number(n));const f=/^after\s+(?<ids>[\d\w- ]+)/.exec(n);if(f!==null){let T=null;for(const $ of f.groups.ids.split(" ")){let b=ct($);b!==void 0&&(!T||b.endTime>T.endTime)&&(T=b)}if(T)return T.endTime;const M=new Date;return M.setHours(0,0,0,0),M}let d=X(n,e.trim(),!0);if(d.isValid())return d.toDate();{ot.debug("Invalid date:"+n),ot.debug("With date format:"+e.trim());const T=new Date(n);if(T===void 0||isNaN(T.getTime())||T.getFullYear()<-1e4||T.getFullYear()>1e4)throw new Error("Invalid date:"+n);return T}},"getStartDate"),Oe=c(function(t){const e=/^(\d+(?:\.\d+)?)([Mdhmswy]|ms)$/.exec(t.trim());return e!==null?[Number.parseFloat(e[1]),e[2]]:[NaN,"ms"]},"parseDuration"),We=c(function(t,e,n,i=!1){n=n.trim();const f=/^until\s+(?<ids>[\d\w- ]+)/.exec(n);if(f!==null){let b=null;for(const C of f.groups.ids.split(" ")){let D=ct(C);D!==void 0&&(!b||D.startTime<b.startTime)&&(b=D)}if(b)return b.startTime;const L=new Date;return L.setHours(0,0,0,0),L}let d=X(n,e.trim(),!0);if(d.isValid())return i&&(d=d.add(1,"d")),d.toDate();let T=X(t);const[M,$]=Oe(n);if(!Number.isNaN(M)){const b=T.add(M,$);b.isValid()&&(T=b)}return T.toDate()},"getEndDate"),Et=0,mt=c(function(t){return t===void 0?(Et=Et+1,"task"+Et):t},"parseId"),gi=c(function(t,e){let n;e.substr(0,1)===":"?n=e.substr(1,e.length):n=e;const i=n.split(","),a={};ne(i,a,Fe);for(let d=0;d<i.length;d++)i[d]=i[d].trim();let f="";switch(i.length){case 1:a.id=mt(),a.startTime=t.endTime,f=i[0];break;case 2:a.id=mt(),a.startTime=Xt(void 0,tt,i[0]),f=i[1];break;case 3:a.id=mt(i[0]),a.startTime=Xt(void 0,tt,i[1]),f=i[2];break}return f&&(a.endTime=We(a.startTime,tt,f,xt),a.manualEndTime=X(f,"YYYY-MM-DD",!0).isValid(),Le(a,tt,vt,pt)),a},"compileData"),pi=c(function(t,e){let n;e.substr(0,1)===":"?n=e.substr(1,e.length):n=e;const i=n.split(","),a={};ne(i,a,Fe);for(let f=0;f<i.length;f++)i[f]=i[f].trim();switch(i.length){case 1:a.id=mt(),a.startTime={type:"prevTaskEnd",id:t},a.endTime={data:i[0]};break;case 2:a.id=mt(),a.startTime={type:"getStartDate",startData:i[0]},a.endTime={data:i[1]};break;case 3:a.id=mt(i[0]),a.startTime={type:"getStartDate",startData:i[1]},a.endTime={data:i[2]};break}return a},"parseData"),jt,It,B=[],Pe={},vi=c(function(t,e){const n={section:kt,type:kt,processed:!1,manualEndTime:!1,renderEndTime:null,raw:{data:e},task:t,classes:[]},i=pi(It,e);n.raw.startTime=i.startTime,n.raw.endTime=i.endTime,n.id=i.id,n.prevTaskId=It,n.active=i.active,n.done=i.done,n.crit=i.crit,n.milestone=i.milestone,n.vert=i.vert,n.order=Gt,Gt++;const a=B.push(n);It=n.id,Pe[n.id]=a-1},"addTask"),ct=c(function(t){const e=Pe[t];return B[e]},"findTaskById"),xi=c(function(t,e){const n={section:kt,type:kt,description:t,task:t,classes:[]},i=gi(jt,e);n.startTime=i.startTime,n.endTime=i.endTime,n.id=i.id,n.active=i.active,n.done=i.done,n.crit=i.crit,n.milestone=i.milestone,n.vert=i.vert,jt=n,$t.push(n)},"addTaskOrg"),ve=c(function(){const t=c(function(n){const i=B[n];let a="";switch(B[n].raw.startTime.type){case"prevTaskEnd":{const f=ct(i.prevTaskId);i.startTime=f.endTime;break}case"getStartDate":a=Xt(void 0,tt,B[n].raw.startTime.startData),a&&(B[n].startTime=a);break}return B[n].startTime&&(B[n].endTime=We(B[n].startTime,tt,B[n].raw.endTime.data,xt),B[n].endTime&&(B[n].processed=!0,B[n].manualEndTime=X(B[n].raw.endTime.data,"YYYY-MM-DD",!0).isValid(),Le(B[n],tt,vt,pt))),B[n].processed},"compileTask");let e=!0;for(const[n,i]of B.entries())t(n),e=e&&i.processed;return e},"compileTasks"),Ti=c(function(t,e){let n=e;ft().securityLevel!=="loose"&&(n=Ue.sanitizeUrl(e)),t.split(",").forEach(function(i){ct(i)!==void 0&&(ze(i,()=>{window.open(n,"_self")}),Kt.set(i,n))}),Ne(t,"clickable")},"setLink"),Ne=c(function(t,e){t.split(",").forEach(function(n){let i=ct(n);i!==void 0&&i.classes.push(e)})},"setClass"),bi=c(function(t,e,n){if(ft().securityLevel!=="loose"||e===void 0)return;let i=[];if(typeof n=="string"){i=n.split(/,(?=(?:(?:[^"]*"){2})*[^"]*$)/);for(let f=0;f<i.length;f++){let d=i[f].trim();d.startsWith('"')&&d.endsWith('"')&&(d=d.substr(1,d.length-2)),i[f]=d}}i.length===0&&i.push(t),ct(t)!==void 0&&ze(t,()=>{Qe.runFunc(e,...i)})},"setClickFun"),ze=c(function(t,e){ee.push(function(){const n=dt?`${dt}-${t}`:t,i=document.querySelector(`[id="${n}"]`);i!==null&&i.addEventListener("click",function(){e()})},function(){const n=dt?`${dt}-${t}`:t,i=document.querySelector(`[id="${n}-text"]`);i!==null&&i.addEventListener("click",function(){e()})})},"pushFun"),wi=c(function(t,e,n){t.split(",").forEach(function(i){bi(i,e,n)}),Ne(t,"clickable")},"setClickEvent"),_i=c(function(t){ee.forEach(function(e){e(t)})},"bindFunctions"),Di={getConfig:c(()=>ft().gantt,"getConfig"),clear:Hr,setDateFormat:Qr,getDateFormat:ni,enableInclusiveEndDates:Kr,endDatesAreInclusive:Jr,enableTopAxis:ti,topAxisEnabled:ei,setAxisFormat:Br,getAxisFormat:Gr,setTickInterval:Xr,getTickInterval:jr,setTodayMarker:Ur,getTodayMarker:Zr,setAccTitle:Ge,getAccTitle:Be,setDiagramTitle:qe,getDiagramTitle:He,setDiagramId:qr,setDisplayMode:ri,getDisplayMode:ii,setAccDescription:Ve,getAccDescription:Re,addSection:ui,getSections:di,getTasks:fi,addTask:vi,findTaskById:ct,addTaskOrg:xi,setIncludes:si,getIncludes:ai,setExcludes:oi,getExcludes:ci,setClickEvent:wi,setLink:Ti,getLinks:li,bindFunctions:_i,parseDuration:Oe,isInvalidDate:Ae,setWeekday:hi,getWeekday:mi,setWeekend:ki};function ne(t,e,n){let i=!0;for(;i;)i=!1,n.forEach(function(a){const f="^\\s*"+a+"\\s*$",d=new RegExp(f);t[0].match(d)&&(e[a]=!0,t.shift(1),i=!0)})}c(ne,"getTaskTags");X.extend(Rr);var Si=c(function(){ot.debug("Something is calling, setConf, remove the call")},"setConf"),xe={monday:or,tuesday:ar,wednesday:sr,thursday:nr,friday:ir,saturday:rr,sunday:er},Ci=c((t,e)=>{let n=[...t].map(()=>-1/0),i=[...t].sort((f,d)=>f.startTime-d.startTime||f.order-d.order),a=0;for(const f of i)for(let d=0;d<n.length;d++)if(f.startTime>=n[d]){n[d]=f.endTime,f.order=d+e,d>a&&(a=d);break}return a},"getMaxIntersections"),rt,Vt=1e4,Mi=c(function(t,e,n,i){const a=ft().gantt;i.db.setDiagramId(e);const f=ft().securityLevel;let d;f==="sandbox"&&(d=bt("#i"+e));const T=f==="sandbox"?bt(d.nodes()[0].contentDocument.body):bt("body"),M=f==="sandbox"?d.nodes()[0].contentDocument:document,$=M.getElementById(e);rt=$.parentElement.offsetWidth,rt===void 0&&(rt=1200),a.useWidth!==void 0&&(rt=a.useWidth);const b=i.db.getTasks();let L=[];for(const u of b)L.push(u.type);L=h(L);const C={};let D=2*a.topPadding;if(i.db.getDisplayMode()==="compact"||a.displayMode==="compact"){const u={};for(const v of b)u[v.section]===void 0?u[v.section]=[v]:u[v.section].push(v);let x=0;for(const v of Object.keys(u)){const k=Ci(u[v],x)+1;x+=k,D+=k*(a.barHeight+a.barGap),C[v]=k}}else{D+=b.length*(a.barHeight+a.barGap);for(const u of L)C[u]=b.filter(x=>x.type===u).length}$.setAttribute("viewBox","0 0 "+rt+" "+D);const R=T.select(`[id="${e}"]`),I=Ke().domain([Je(b,function(u){return u.startTime}),tr(b,function(u){return u.endTime})]).rangeRound([0,rt-a.leftPadding-a.rightPadding]);function S(u,x){const v=u.startTime,k=x.startTime;let m=0;return v>k?m=1:v<k&&(m=-1),m}c(S,"taskCompare"),b.sort(S),E(b,rt,D),Xe(R,D,rt,a.useMaxWidth),R.append("text").text(i.db.getDiagramTitle()).attr("x",rt/2).attr("y",a.titleTopMargin).attr("class","titleText");function E(u,x,v){const k=a.barHeight,m=k+a.barGap,o=a.topPadding,l=a.leftPadding,y=cr().domain([0,L.length]).range(["#00B9FA","#F95002"]).interpolate(Dr);A(m,o,l,x,v,u,i.db.getExcludes(),i.db.getIncludes()),H(l,o,x,v),W(u,m,o,l,k,y,x),Y(m,o),p(l,o,x,v)}c(E,"makeGantt");function W(u,x,v,k,m,o,l){u.sort((r,_)=>r.vert===_.vert?0:r.vert?1:-1);const g=[...new Set(u.map(r=>r.order))].map(r=>u.find(_=>_.order===r));R.append("g").selectAll("rect").data(g).enter().append("rect").attr("x",0).attr("y",function(r,_){return _=r.order,_*x+v-2}).attr("width",function(){return l-a.rightPadding/2}).attr("height",x).attr("class",function(r){for(const[_,N]of L.entries())if(r.type===N)return"section section"+_%a.numberSectionStyles;return"section section0"}).enter();const w=R.append("g").selectAll("rect").data(u).enter(),s=i.db.getLinks();if(w.append("rect").attr("id",function(r){return e+"-"+r.id}).attr("rx",3).attr("ry",3).attr("x",function(r){return r.milestone?I(r.startTime)+k+.5*(I(r.endTime)-I(r.startTime))-.5*m:I(r.startTime)+k}).attr("y",function(r,_){return _=r.order,r.vert?a.gridLineStartPadding:_*x+v}).attr("width",function(r){return r.milestone?m:r.vert?.08*m:I(r.renderEndTime||r.endTime)-I(r.startTime)}).attr("height",function(r){return r.vert?b.length*(a.barHeight+a.barGap)+a.barHeight*2:m}).attr("transform-origin",function(r,_){return _=r.order,(I(r.startTime)+k+.5*(I(r.endTime)-I(r.startTime))).toString()+"px "+(_*x+v+.5*m).toString()+"px"}).attr("class",function(r){const _="task";let N="";r.classes.length>0&&(N=r.classes.join(" "));let P=0;for(const[G,F]of L.entries())r.type===F&&(P=G%a.numberSectionStyles);let O="";return r.active?r.crit?O+=" activeCrit":O=" active":r.done?r.crit?O=" doneCrit":O=" done":r.crit&&(O+=" crit"),O.length===0&&(O=" task"),r.milestone&&(O=" milestone "+O),r.vert&&(O=" vert "+O),O+=P,O+=" "+N,_+O}),w.append("text").attr("id",function(r){return e+"-"+r.id+"-text"}).text(function(r){return r.task}).attr("font-size",a.fontSize).attr("x",function(r){let _=I(r.startTime),N=I(r.renderEndTime||r.endTime);if(r.milestone&&(_+=.5*(I(r.endTime)-I(r.startTime))-.5*m,N=_+m),r.vert)return I(r.startTime)+k;const P=this.getBBox().width;return P>N-_?N+P+1.5*a.leftPadding>l?_+k-5:N+k+5:(N-_)/2+_+k}).attr("y",function(r,_){return r.vert?a.gridLineStartPadding+b.length*(a.barHeight+a.barGap)+60:(_=r.order,_*x+a.barHeight/2+(a.fontSize/2-2)+v)}).attr("text-height",m).attr("class",function(r){const _=I(r.startTime);let N=I(r.endTime);r.milestone&&(N=_+m);const P=this.getBBox().width;let O="";r.classes.length>0&&(O=r.classes.join(" "));let G=0;for(const[Q,nt]of L.entries())r.type===nt&&(G=Q%a.numberSectionStyles);let F="";return r.active&&(r.crit?F="activeCritText"+G:F="activeText"+G),r.done?r.crit?F=F+" doneCritText"+G:F=F+" doneText"+G:r.crit&&(F=F+" critText"+G),r.milestone&&(F+=" milestoneText"),r.vert&&(F+=" vertText"),P>N-_?N+P+1.5*a.leftPadding>l?O+" taskTextOutsideLeft taskTextOutside"+G+" "+F:O+" taskTextOutsideRight taskTextOutside"+G+" "+F+" width-"+P:O+" taskText taskText"+G+" "+F+" width-"+P}),ft().securityLevel==="sandbox"){let r;r=bt("#i"+e);const _=r.nodes()[0].contentDocument;w.filter(function(N){return s.has(N.id)}).each(function(N){var P=_.querySelector("#"+CSS.escape(e+"-"+N.id)),O=_.querySelector("#"+CSS.escape(e+"-"+N.id+"-text"));const G=P.parentNode;var F=_.createElement("a");F.setAttribute("xlink:href",s.get(N.id)),F.setAttribute("target","_top"),G.appendChild(F),F.appendChild(P),F.appendChild(O)})}}c(W,"drawRects");function A(u,x,v,k,m,o,l,y){if(l.length===0&&y.length===0)return;let g,w;for(const{startTime:P,endTime:O}of o)(g===void 0||P<g)&&(g=P),(w===void 0||O>w)&&(w=O);if(!g||!w)return;if(X(w).diff(X(g),"year")>5){ot.warn("The difference between the min and max time is more than 5 years. This will cause performance issues. Skipping drawing exclude days.");return}const s=i.db.getDateFormat(),z=[];let r=null,_=X(g);for(;_.valueOf()<=w;)i.db.isInvalidDate(_,s,l,y)?r?r.end=_:r={start:_,end:_}:r&&(z.push(r),r=null),_=_.add(1,"d");R.append("g").selectAll("rect").data(z).enter().append("rect").attr("id",P=>e+"-exclude-"+P.start.format("YYYY-MM-DD")).attr("x",P=>I(P.start.startOf("day"))+v).attr("y",a.gridLineStartPadding).attr("width",P=>I(P.end.endOf("day"))-I(P.start.startOf("day"))).attr("height",m-x-a.gridLineStartPadding).attr("transform-origin",function(P,O){return(I(P.start)+v+.5*(I(P.end)-I(P.start))).toString()+"px "+(O*u+.5*m).toString()+"px"}).attr("class","exclude-range")}c(A,"drawExcludeDays");function V(u,x,v,k){if(v<=0||u>x)return 1/0;const m=x-u,o=X.duration({[k??"day"]:v}).asMilliseconds();return o<=0?1/0:Math.ceil(m/o)}c(V,"getEstimatedTickCount");function H(u,x,v,k){const m=i.db.getDateFormat(),o=i.db.getAxisFormat();let l;o?l=o:m==="D"?l="%d":l=a.axisFormat??"%Y-%m-%d";let y=pr(I).tickSize(-k+x+a.gridLineStartPadding).tickFormat(ae(l));const w=/^([1-9]\d*)(millisecond|second|minute|hour|day|week|month)$/.exec(i.db.getTickInterval()||a.tickInterval);if(w!==null){const s=parseInt(w[1],10);if(isNaN(s)||s<=0)ot.warn(`Invalid tick interval value: "${w[1]}". Skipping custom tick interval.`);else{const z=w[2],r=i.db.getWeekday()||a.weekday,_=I.domain(),N=_[0],P=_[1],O=V(N,P,s,z);if(O>Vt)ot.warn(`The tick interval "${s}${z}" would generate ${O} ticks, which exceeds the maximum allowed (${Vt}). This may indicate an invalid date or time range. Skipping custom tick interval.`);else switch(z){case"millisecond":y.ticks(fe.every(s));break;case"second":y.ticks(de.every(s));break;case"minute":y.ticks(ue.every(s));break;case"hour":y.ticks(le.every(s));break;case"day":y.ticks(ce.every(s));break;case"week":y.ticks(xe[r].every(s));break;case"month":y.ticks(oe.every(s));break}}}if(R.append("g").attr("class","grid").attr("transform","translate("+u+", "+(k-50)+")").call(y).selectAll("text").style("text-anchor","middle").attr("fill","#000").attr("stroke","none").attr("font-size",10).attr("dy","1em"),i.db.topAxisEnabled()||a.topAxis){let s=gr(I).tickSize(-k+x+a.gridLineStartPadding).tickFormat(ae(l));if(w!==null){const z=parseInt(w[1],10);if(isNaN(z)||z<=0)ot.warn(`Invalid tick interval value: "${w[1]}". Skipping custom tick interval.`);else{const r=w[2],_=i.db.getWeekday()||a.weekday,N=I.domain(),P=N[0],O=N[1];if(V(P,O,z,r)<=Vt)switch(r){case"millisecond":s.ticks(fe.every(z));break;case"second":s.ticks(de.every(z));break;case"minute":s.ticks(ue.every(z));break;case"hour":s.ticks(le.every(z));break;case"day":s.ticks(ce.every(z));break;case"week":s.ticks(xe[_].every(z));break;case"month":s.ticks(oe.every(z));break}}}R.append("g").attr("class","grid").attr("transform","translate("+u+", "+x+")").call(s).selectAll("text").style("text-anchor","middle").attr("fill","#000").attr("stroke","none").attr("font-size",10)}}c(H,"makeGrid");function Y(u,x){let v=0;const k=Object.keys(C).map(m=>[m,C[m]]);R.append("g").selectAll("text").data(k).enter().append(function(m){const o=m[0].split(je.lineBreakRegex),l=-(o.length-1)/2,y=M.createElementNS("http://www.w3.org/2000/svg","text");y.setAttribute("dy",l+"em");for(const[g,w]of o.entries()){const s=M.createElementNS("http://www.w3.org/2000/svg","tspan");s.setAttribute("alignment-baseline","central"),s.setAttribute("x","10"),g>0&&s.setAttribute("dy","1em"),s.textContent=w,y.appendChild(s)}return y}).attr("x",10).attr("y",function(m,o){if(o>0)for(let l=0;l<o;l++)return v+=k[o-1][1],m[1]*u/2+v*u+x;else return m[1]*u/2+x}).attr("font-size",a.sectionFontSize).attr("class",function(m){for(const[o,l]of L.entries())if(m[0]===l)return"sectionTitle sectionTitle"+o%a.numberSectionStyles;return"sectionTitle"})}c(Y,"vertLabels");function p(u,x,v,k){const m=i.db.getTodayMarker();if(m==="off")return;const o=R.append("g").attr("class","today"),l=new Date,y=o.append("line");y.attr("x1",I(l)+u).attr("x2",I(l)+u).attr("y1",a.titleTopMargin).attr("y2",k-a.titleTopMargin).attr("class","today"),m!==""&&y.attr("style",m.replace(/,/g,";"))}c(p,"drawToday");function h(u){const x={},v=[];for(let k=0,m=u.length;k<m;++k)Object.prototype.hasOwnProperty.call(x,u[k])||(x[u[k]]=!0,v.push(u[k]));return v}c(h,"checkUnique")},"draw"),Ei={setConf:Si,draw:Mi},Ii=c(t=>`
  .mermaid-main-font {
        font-family: ${t.fontFamily};
  }

  .exclude-range {
    fill: ${t.excludeBkgColor};
  }

  .section {
    stroke: none;
    opacity: 0.2;
  }

  .section0 {
    fill: ${t.sectionBkgColor};
  }

  .section2 {
    fill: ${t.sectionBkgColor2};
  }

  .section1,
  .section3 {
    fill: ${t.altSectionBkgColor};
    opacity: 0.2;
  }

  .sectionTitle0 {
    fill: ${t.titleColor};
  }

  .sectionTitle1 {
    fill: ${t.titleColor};
  }

  .sectionTitle2 {
    fill: ${t.titleColor};
  }

  .sectionTitle3 {
    fill: ${t.titleColor};
  }

  .sectionTitle {
    text-anchor: start;
    font-family: ${t.fontFamily};
  }


  /* Grid and axis */

  .grid .tick {
    stroke: ${t.gridColor};
    opacity: 0.8;
    shape-rendering: crispEdges;
  }

  .grid .tick text {
    font-family: ${t.fontFamily};
    fill: ${t.textColor};
  }

  .grid path {
    stroke-width: 0;
  }


  /* Today line */

  .today {
    fill: none;
    stroke: ${t.todayLineColor};
    stroke-width: 2px;
  }


  /* Task styling */

  /* Default task */

  .task {
    stroke-width: 2;
  }

  .taskText {
    text-anchor: middle;
    font-family: ${t.fontFamily};
  }

  .taskTextOutsideRight {
    fill: ${t.taskTextDarkColor};
    text-anchor: start;
    font-family: ${t.fontFamily};
  }

  .taskTextOutsideLeft {
    fill: ${t.taskTextDarkColor};
    text-anchor: end;
  }


  /* Special case clickable */

  .task.clickable {
    cursor: pointer;
  }

  .taskText.clickable {
    cursor: pointer;
    fill: ${t.taskTextClickableColor} !important;
    font-weight: bold;
  }

  .taskTextOutsideLeft.clickable {
    cursor: pointer;
    fill: ${t.taskTextClickableColor} !important;
    font-weight: bold;
  }

  .taskTextOutsideRight.clickable {
    cursor: pointer;
    fill: ${t.taskTextClickableColor} !important;
    font-weight: bold;
  }


  /* Specific task settings for the sections*/

  .taskText0,
  .taskText1,
  .taskText2,
  .taskText3 {
    fill: ${t.taskTextColor};
  }

  .task0,
  .task1,
  .task2,
  .task3 {
    fill: ${t.taskBkgColor};
    stroke: ${t.taskBorderColor};
  }

  .taskTextOutside0,
  .taskTextOutside2
  {
    fill: ${t.taskTextOutsideColor};
  }

  .taskTextOutside1,
  .taskTextOutside3 {
    fill: ${t.taskTextOutsideColor};
  }


  /* Active task */

  .active0,
  .active1,
  .active2,
  .active3 {
    fill: ${t.activeTaskBkgColor};
    stroke: ${t.activeTaskBorderColor};
  }

  .activeText0,
  .activeText1,
  .activeText2,
  .activeText3 {
    fill: ${t.taskTextDarkColor} !important;
  }


  /* Completed task */

  .done0,
  .done1,
  .done2,
  .done3 {
    stroke: ${t.doneTaskBorderColor};
    fill: ${t.doneTaskBkgColor};
    stroke-width: 2;
  }

  .doneText0,
  .doneText1,
  .doneText2,
  .doneText3 {
    fill: ${t.taskTextDarkColor} !important;
  }

  /* Done task text displayed outside the bar sits against the diagram background,
     not against the done-task bar, so it must use the outside/contrast color. */
  .doneText0.taskTextOutsideLeft,
  .doneText0.taskTextOutsideRight,
  .doneText1.taskTextOutsideLeft,
  .doneText1.taskTextOutsideRight,
  .doneText2.taskTextOutsideLeft,
  .doneText2.taskTextOutsideRight,
  .doneText3.taskTextOutsideLeft,
  .doneText3.taskTextOutsideRight {
    fill: ${t.taskTextOutsideColor} !important;
  }


  /* Tasks on the critical line */

  .crit0,
  .crit1,
  .crit2,
  .crit3 {
    stroke: ${t.critBorderColor};
    fill: ${t.critBkgColor};
    stroke-width: 2;
  }

  .activeCrit0,
  .activeCrit1,
  .activeCrit2,
  .activeCrit3 {
    stroke: ${t.critBorderColor};
    fill: ${t.activeTaskBkgColor};
    stroke-width: 2;
  }

  .doneCrit0,
  .doneCrit1,
  .doneCrit2,
  .doneCrit3 {
    stroke: ${t.critBorderColor};
    fill: ${t.doneTaskBkgColor};
    stroke-width: 2;
    cursor: pointer;
    shape-rendering: crispEdges;
  }

  .milestone {
    transform: rotate(45deg) scale(0.8,0.8);
  }

  .milestoneText {
    font-style: italic;
  }
  .doneCritText0,
  .doneCritText1,
  .doneCritText2,
  .doneCritText3 {
    fill: ${t.taskTextDarkColor} !important;
  }

  /* Done-crit task text outside the bar — same reasoning as doneText above. */
  .doneCritText0.taskTextOutsideLeft,
  .doneCritText0.taskTextOutsideRight,
  .doneCritText1.taskTextOutsideLeft,
  .doneCritText1.taskTextOutsideRight,
  .doneCritText2.taskTextOutsideLeft,
  .doneCritText2.taskTextOutsideRight,
  .doneCritText3.taskTextOutsideLeft,
  .doneCritText3.taskTextOutsideRight {
    fill: ${t.taskTextOutsideColor} !important;
  }

  .vert {
    stroke: ${t.vertLineColor};
  }

  .vertText {
    font-size: 15px;
    text-anchor: middle;
    fill: ${t.vertLineColor} !important;
  }

  .activeCritText0,
  .activeCritText1,
  .activeCritText2,
  .activeCritText3 {
    fill: ${t.taskTextDarkColor} !important;
  }

  .titleText {
    text-anchor: middle;
    font-size: 18px;
    fill: ${t.titleColor||t.textColor};
    font-family: ${t.fontFamily};
  }
`,"getStyles"),Yi=Ii,Ni={parser:Vr,db:Di,renderer:Ei,styles:Yi};export{Ni as diagram};
//# sourceMappingURL=ganttDiagram-6RSMTGT7-D-xcx_Ja.js.map
