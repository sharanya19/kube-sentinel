{{- define "ks.name" -}}kube-sentinel{{- end -}}

{{- define "ks.labels" -}}
app.kubernetes.io/name: kube-sentinel
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "ks.selector" -}}
app.kubernetes.io/name: kube-sentinel
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
