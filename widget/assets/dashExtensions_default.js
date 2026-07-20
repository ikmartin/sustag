window.dashExtensions = Object.assign({}, window.dashExtensions, {
    default: {
        function0: function(feature, context) {
            return {
                color: '#0284c7',
                weight: 1,
                fillColor: feature.properties.color,
                fillOpacity: 0.55
            };
        },
        function1: function(feature, context) {
            return {
                weight: 3,
                color: '#0c4a6e',
                fillOpacity: 0.8
            };
        },
        function2: function(feature, layer, context) {
            if (feature.properties && feature.properties.tooltip) {
                // Click opens a popup — reliable even when Leaflet's vector hover
                // hit-testing goes stale after a map pan/zoom (clicks are hit-tested
                // by coordinate, so they always reach the cell).
                layer.bindPopup(feature.properties.tooltip);
                // Hover tooltip too, for the (intermittent) cases where mouseover fires.
                layer.bindTooltip(feature.properties.tooltip, {
                    sticky: true
                });
                layer.on('mouseover', function(e) {
                    layer.openTooltip(e.latlng);
                });
                layer.on('mousemove', function(e) {
                    layer.openTooltip(e.latlng);
                });
                layer.on('mouseout', function() {
                    layer.closeTooltip();
                });
            }
        }
    }
});